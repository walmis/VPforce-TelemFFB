"""
Comprehensive unit tests for MfsfXpSteeringFrictionEffectMixIn.

This module tests the steering friction effect for MSFS aircraft on ground,
including various scenarios like different speeds, surfaces, and water operations.
"""
import pytest
from tests.framework.base import BaseTelemetryEffectTestCase
from tests.framework.utils import (
    TelemetryDataBuilder,
    assert_effect_started,
    assert_effect_stopped,
    assert_friction_coefficient_in_range,
)
from telemffb.sim.msfs_xp.MfsfXpSteeringFrictionEffectMixIn import MfsfXpSteeringFrictionEffectMixIn


class TestMfsfXpSteeringFrictionEffect(BaseTelemetryEffectTestCase):
    """Test suite for MSFS steering friction effect."""
    
    def test_effect_disabled_by_default(self):
        """Test that effect does not activate when disabled."""
        # Arrange
        instance = self.create_test_instance(MfsfXpSteeringFrictionEffectMixIn)
        instance._test_sim_is_msfs = True
        instance.steering_friction = 0  # Disabled
        instance.enable_friction_ovd = True
        
        telem = TelemetryDataBuilder().ffb_type("pedals").on_ground().build()
        
        # Act
        instance.on_telemetry(telem)
        
        # Assert
        effect = self.mock_effects['friction']
        assert_effect_stopped(effect, "Effect should not start when disabled")
    
    def test_effect_requires_pedals(self):
        """Test that effect only works with pedals FFB type."""
        # Arrange
        instance = self.create_test_instance(MfsfXpSteeringFrictionEffectMixIn)
        instance._test_sim_is_msfs = True
        instance.steering_friction = 1
        instance.enable_friction_ovd = True
        
        telem = TelemetryDataBuilder().ffb_type("joystick").on_ground().build()
        
        # Act
        instance.on_telemetry(telem)
        
        # Assert
        effect = self.mock_effects['friction']
        assert not effect.started, "Effect should not start for joystick"
    
    def test_effect_requires_msfs(self):
        """Test that effect only works with MSFS simulator."""
        # Arrange
        instance = self.create_test_instance(MfsfXpSteeringFrictionEffectMixIn)
        instance._test_sim_is_msfs = False
        instance._test_sim_is_xplane = True
        instance.steering_friction = 1
        instance.enable_friction_ovd = True
        
        telem = TelemetryDataBuilder().ffb_type("pedals").on_ground().build()
        
        # Act
        instance.on_telemetry(telem)
        
        # Assert
        effect = self.mock_effects['friction']
        assert not effect.started, "Effect should not start for X-Plane"
    
    def test_effect_requires_friction_override_enabled(self):
        """Test that effect flags error when friction override not enabled."""
        # Arrange
        instance = self.create_test_instance(MfsfXpSteeringFrictionEffectMixIn)
        instance._test_sim_is_msfs = True
        instance.steering_friction = 1
        instance.enable_friction_ovd = False  # Not enabled
        
        telem = TelemetryDataBuilder().ffb_type("pedals").on_ground().build()
        
        # Act
        instance.on_telemetry(telem)
        
        # Assert
        assert hasattr(instance, '_flagged_errors')
        assert len(instance._flagged_errors) > 0
        assert "friction override not enabled" in instance._flagged_errors[0].lower()
    
    def test_effect_activates_on_ground_with_weight(self):
        """Test that effect activates when aircraft is on ground with weight on wheels."""
        # Arrange
        instance = self.create_test_instance(MfsfXpSteeringFrictionEffectMixIn)
        instance._test_sim_is_msfs = True
        instance.steering_friction = 1
        instance.enable_friction_ovd = True
        instance.friction_force = 0.2  # Base friction
        
        telem = (
            TelemetryDataBuilder()
            .ffb_type("pedals")
            .on_ground()
            .weight_on_wheels(1.0, 1.0, 1.0)
            .ground_speed(10.0)
            .build()
        )
        
        # Act
        instance.on_telemetry(telem)
        
        # Assert
        effect = self.mock_effects['friction']
        assert_effect_started(effect, "Effect should start on ground with weight")
        assert effect.name == "steering_friction"
        assert instance.friction_effect_overridden
    
    def test_friction_scales_with_speed(self):
        """Test that friction coefficient decreases with increasing speed."""
        # Arrange
        instance = self.create_test_instance(MfsfXpSteeringFrictionEffectMixIn)
        instance._test_sim_is_msfs = True
        instance.steering_friction = 1
        instance.enable_friction_ovd = True
        instance.friction_force = 0.0  # Zero base friction for clearer testing
        instance.steering_friction_intensity = 1.0
        instance.steering_friction_expo = 0.0  # Linear for predictable testing
        
        # Test at low speed (high friction)
        telem_low_speed = (
            TelemetryDataBuilder()
            .ffb_type("pedals")
            .on_ground()
            .weight_on_wheels(1.0, 1.0, 1.0)
            .ground_speed(0.0)  # Stationary
            .build()
        )
        
        instance.on_telemetry(telem_low_speed)
        effect = self.mock_effects['friction']
        low_speed_coeff = effect.get_coefficients()[0]
        
        # Test at medium speed
        telem_med_speed = (
            TelemetryDataBuilder()
            .ffb_type("pedals")
            .on_ground()
            .weight_on_wheels(1.0, 1.0, 1.0)
            .ground_speed(10.0)
            .build()
        )
        
        instance.on_telemetry(telem_med_speed)
        med_speed_coeff = effect.get_coefficients()[0]
        
        # Test at high speed (low friction)
        telem_high_speed = (
            TelemetryDataBuilder()
            .ffb_type("pedals")
            .on_ground()
            .weight_on_wheels(1.0, 1.0, 1.0)
            .ground_speed(20.0)
            .build()
        )
        
        instance.on_telemetry(telem_high_speed)
        high_speed_coeff = effect.get_coefficients()[0]
        
        # Assert
        assert low_speed_coeff > med_speed_coeff, (
            f"Friction at 0kt ({low_speed_coeff}) should be > friction at 10kt ({med_speed_coeff})"
        )
        assert med_speed_coeff > high_speed_coeff, (
            f"Friction at 10kt ({med_speed_coeff}) should be > friction at 20kt ({high_speed_coeff})"
        )
    
    def test_friction_intensity_parameter(self):
        """Test that steering_friction_intensity scales the effect magnitude."""
        # Arrange
        instance = self.create_test_instance(MfsfXpSteeringFrictionEffectMixIn)
        instance._test_sim_is_msfs = True
        instance.steering_friction = 1
        instance.enable_friction_ovd = True
        instance.friction_force = 0.0
        instance.steering_friction_expo = 0.0
        
        telem = (
            TelemetryDataBuilder()
            .ffb_type("pedals")
            .on_ground()
            .weight_on_wheels(1.0, 1.0, 1.0)
            .ground_speed(5.0)
            .build()
        )
        
        # Test with low intensity
        instance.steering_friction_intensity = 0.2
        instance.on_telemetry(telem)
        effect = self.mock_effects['friction']
        low_intensity_coeff = effect.get_coefficients()[0]
        
        # Test with high intensity
        instance.steering_friction_intensity = 1.0
        instance.on_telemetry(telem)
        high_intensity_coeff = effect.get_coefficients()[0]
        
        # Assert
        assert high_intensity_coeff > low_intensity_coeff, (
            f"High intensity ({high_intensity_coeff}) should produce more friction "
            f"than low intensity ({low_intensity_coeff})"
        )
    
    def test_water_surface_with_rudder(self):
        """Test friction behavior on water surface with water rudder."""
        # Arrange
        instance = self.create_test_instance(MfsfXpSteeringFrictionEffectMixIn)
        instance._test_sim_is_msfs = True
        instance.steering_friction = 1
        instance.enable_friction_ovd = True
        instance.friction_force = 0.0
        
        # Test with fully extended water rudder
        telem_full_rudder = (
            TelemetryDataBuilder()
            .ffb_type("pedals")
            .on_ground()
            .weight_on_wheels(0, 0, 0)  # No weight on wheels (in water)
            .surface_type("Water")
            .water_rudder(1.0)  # Fully extended
            .ground_speed(5.0)
            .build()
        )
        
        instance.on_telemetry(telem_full_rudder)
        effect = self.mock_effects['friction']
        full_rudder_coeff = effect.get_coefficients()[0]
        
        # Test with partially extended water rudder
        telem_half_rudder = (
            TelemetryDataBuilder()
            .ffb_type("pedals")
            .on_ground()
            .weight_on_wheels(0, 0, 0)
            .surface_type("Water")
            .water_rudder(0.5)  # Half extended
            .ground_speed(5.0)
            .build()
        )
        
        instance.on_telemetry(telem_half_rudder)
        half_rudder_coeff = effect.get_coefficients()[0]
        
        # Test with retracted water rudder
        telem_no_rudder = (
            TelemetryDataBuilder()
            .ffb_type("pedals")
            .on_ground()
            .weight_on_wheels(0, 0, 0)
            .surface_type("Water")
            .water_rudder(0.0)  # Retracted
            .ground_speed(5.0)
            .build()
        )
        
        instance.on_telemetry(telem_no_rudder)
        no_rudder_coeff = effect.get_coefficients()[0]
        
        # Assert
        assert full_rudder_coeff > half_rudder_coeff, (
            "Full rudder extension should produce more friction than half"
        )
        assert half_rudder_coeff > no_rudder_coeff, (
            "Half rudder extension should produce more friction than none"
        )
        assert no_rudder_coeff == 0, (
            "Retracted water rudder should produce no friction"
        )
    
    def test_effect_cleanup_when_airborne(self):
        """Test that effect properly cleans up when aircraft goes airborne."""
        # Arrange
        instance = self.create_test_instance(MfsfXpSteeringFrictionEffectMixIn)
        instance._test_sim_is_msfs = True
        instance.steering_friction = 1
        instance.enable_friction_ovd = True
        
        # First activate the effect on ground
        telem_ground = (
            TelemetryDataBuilder()
            .ffb_type("pedals")
            .on_ground()
            .weight_on_wheels(1.0, 1.0, 1.0)
            .ground_speed(10.0)
            .build()
        )
        
        instance.on_telemetry(telem_ground)
        effect = self.mock_effects['friction']
        assert effect.started, "Effect should be active on ground"
        assert instance.friction_effect_overridden
        
        # Then lift off
        telem_airborne = (
            TelemetryDataBuilder()
            .ffb_type("pedals")
            .on_ground(False)
            .weight_on_wheels(0, 0, 0)
            .ground_speed(60.0)
            .build()
        )
        
        # Act
        instance.on_telemetry(telem_airborne)
        
        # Assert
        assert effect.destroy_count > 0, "Effect should be destroyed when airborne"
        assert not instance.friction_effect_overridden, (
            "Override flag should be cleared when airborne"
        )
    
    def test_effect_cleanup_when_disabled(self):
        """Test that effect properly cleans up when disabled after being active."""
        # Arrange
        instance = self.create_test_instance(MfsfXpSteeringFrictionEffectMixIn)
        instance._test_sim_is_msfs = True
        instance.steering_friction = 1
        instance.enable_friction_ovd = True
        
        # First activate the effect
        telem = (
            TelemetryDataBuilder()
            .ffb_type("pedals")
            .on_ground()
            .weight_on_wheels(1.0, 1.0, 1.0)
            .ground_speed(10.0)
            .build()
        )
        
        instance.on_telemetry(telem)
        effect = self.mock_effects['friction']
        initial_destroy_count = effect.destroy_count
        assert effect.started
        
        # Then disable the effect
        instance.steering_friction = 0
        
        # Act
        instance.on_telemetry(telem)
        
        # Assert
        assert effect.destroy_count > initial_destroy_count, (
            "Effect should be destroyed when disabled"
        )
        assert not instance.friction_effect_overridden
    
    def test_friction_respects_base_friction_force(self):
        """Test that effect respects base friction_force setting."""
        # Arrange
        instance = self.create_test_instance(MfsfXpSteeringFrictionEffectMixIn)
        instance._test_sim_is_msfs = True
        instance.steering_friction = 1
        instance.enable_friction_ovd = True
        instance.steering_friction_intensity = 0.0  # Zero intensity to test baseline
        instance.friction_force = 0.5  # 50% base friction
        
        telem = (
            TelemetryDataBuilder()
            .ffb_type("pedals")
            .on_ground()
            .weight_on_wheels(1.0, 1.0, 1.0)
            .ground_speed(0.0)
            .build()
        )
        
        # Act
        instance.on_telemetry(telem)
        
        # Assert
        effect = self.mock_effects['friction']
        coeff = effect.get_coefficients()[0]
        expected_base = int(0.5 * 4096)
        assert coeff >= expected_base, (
            f"Friction coefficient ({coeff}) should be at least base friction ({expected_base})"
        )
    
    def test_friction_coefficient_clamped_to_maximum(self):
        """Test that friction coefficient never exceeds maximum value of 4096."""
        # Arrange
        instance = self.create_test_instance(MfsfXpSteeringFrictionEffectMixIn)
        instance._test_sim_is_msfs = True
        instance.steering_friction = 1
        instance.enable_friction_ovd = True
        instance.friction_force = 1.0  # Maximum base
        instance.steering_friction_intensity = 10.0  # Excessive intensity
        
        telem = (
            TelemetryDataBuilder()
            .ffb_type("pedals")
            .on_ground()
            .weight_on_wheels(1.0, 1.0, 1.0)
            .ground_speed(0.0)
            .build()
        )
        
        # Act
        instance.on_telemetry(telem)
        
        # Assert
        effect = self.mock_effects['friction']
        coeff = effect.get_coefficients()[0]
        assert coeff <= 4096, f"Friction coefficient ({coeff}) should not exceed 4096"
    
    def test_telemetry_data_updated(self):
        """Test that telemetry data includes steering friction percentage."""
        # Arrange
        instance = self.create_test_instance(MfsfXpSteeringFrictionEffectMixIn)
        instance._test_sim_is_msfs = True
        instance.steering_friction = 1
        instance.enable_friction_ovd = True
        instance.friction_force = 0.0
        instance.steering_friction_intensity = 1.0
        
        telem = (
            TelemetryDataBuilder()
            .ffb_type("pedals")
            .on_ground()
            .weight_on_wheels(1.0, 1.0, 1.0)
            .ground_speed(10.0)
            .build()
        )
        
        # Act
        instance.on_telemetry(telem)
        
        # Assert
        assert "_pct_steer_f" in telem, "Telemetry should include steering friction percentage"
        assert 0.0 <= telem["_pct_steer_f"] <= 1.0, (
            f"Steering friction percentage should be 0-1, got {telem['_pct_steer_f']}"
        )
    
    def test_expo_curve_effect(self):
        """Test that expo parameter affects the friction curve."""
        # Arrange
        instance = self.create_test_instance(MfsfXpSteeringFrictionEffectMixIn)
        instance._test_sim_is_msfs = True
        instance.steering_friction = 1
        instance.enable_friction_ovd = True
        instance.friction_force = 0.0
        instance.steering_friction_intensity = 1.0
        
        telem = (
            TelemetryDataBuilder()
            .ffb_type("pedals")
            .on_ground()
            .weight_on_wheels(1.0, 1.0, 1.0)
            .ground_speed(10.0)
            .build()
        )
        
        # Test with negative expo (more responsive at low speeds)
        instance.steering_friction_expo = -0.4
        instance.on_telemetry(telem)
        effect = self.mock_effects['friction']
        negative_expo_coeff = effect.get_coefficients()[0]
        
        # Test with positive expo (less responsive at low speeds)
        instance.steering_friction_expo = 0.4
        instance.on_telemetry(telem)
        positive_expo_coeff = effect.get_coefficients()[0]
        
        # Assert - coefficients should differ based on expo
        assert negative_expo_coeff != positive_expo_coeff, (
            "Different expo values should produce different friction coefficients"
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
