"""
Simple working example tests to demonstrate the testing framework.

These tests verify the framework works correctly and provide examples
of different testing patterns.
"""
import pytest
from tests.framework.base import BaseTelemetryEffectTestCase, MockConditionEffect
from tests.framework.utils import (
    TelemetryDataBuilder,
    assert_effect_started,
    assert_friction_coefficient_in_range,
)
from telemffb.sim.BaseTelemetryData import BaseTelemetryData
from telemffb.sim.msfs_xp.MfsfXpSteeringFrictionEffectMixIn import MfsfXpSteeringFrictionEffectMixIn


class TestFrameworkBasics(BaseTelemetryEffectTestCase):
    """Basic tests demonstrating the framework works correctly."""
    
    def test_can_create_test_instance(self):
        """Test that we can create a test instance of a mixin."""
        instance = self.create_test_instance(MfsfXpSteeringFrictionEffectMixIn)
        
        assert instance is not None
        assert hasattr(instance, 'steering_friction')
        assert hasattr(instance, 'effects')
    
    def test_default_parameters(self):
        """Test that default parameters are set correctly."""
        instance = self.create_test_instance(MfsfXpSteeringFrictionEffectMixIn)
        
        assert instance.steering_friction == 0
        assert instance.steering_friction_intensity == 0.8
        assert instance.steering_friction_spring == 0.5
        assert instance.steering_friction_expo == -0.4
    
    def test_can_override_parameters(self):
        """Test that we can override parameters during creation."""
        instance = self.create_test_instance(
            MfsfXpSteeringFrictionEffectMixIn,
            steering_friction=1,
            steering_friction_intensity=0.5
        )
        
        assert instance.steering_friction == 1
        assert instance.steering_friction_intensity == 0.5
    
    def test_mock_device_works(self):
        """Test that mock FFB device works correctly."""
        self.mock_device._input_data.set_axis(x=0.5, y=-0.3)
        
        x, y = self.mock_device._input_data.axisXY()
        
        assert x == 0.5
        assert y == -0.3
    
    def test_mock_effects_dispenser_works(self):
        """Test that mock effects dispenser provides effects."""
        effect = self.mock_effects['friction']
        
        assert effect is not None
        assert isinstance(effect, MockConditionEffect)
        assert hasattr(effect, 'friction')
        assert hasattr(effect, 'start')
    
    def test_mock_effect_tracking(self):
        """Test that mock effects track their usage."""
        effect = self.mock_effects['test_effect']
        
        # Initially not started
        assert not effect.started
        assert effect.start_count == 0
        
        # Start the effect
        effect.friction(2048, 2048).start()
        
        # Verify tracking
        assert effect.started
        assert effect.start_count == 1
        
        # Stop the effect
        effect.stop()
        
        # Verify tracking
        assert not effect.started
        assert effect.stop_count == 1
    
    def test_mock_simconnect_works(self):
        """Test that mock SimConnect tracks events."""
        self.mock_simconnect.send_event_to_msfs("TEST_EVENT", 12345)
        
        events = self.mock_simconnect.sent_events
        assert len(events) == 1
        assert events[0] == ("TEST_EVENT", 12345)
        
        last_event = self.mock_simconnect.get_last_event()
        assert last_event == ("TEST_EVENT", 12345)


class TestTelemetryDataBuilder(BaseTelemetryEffectTestCase):
    """Tests for the TelemetryDataBuilder."""
    
    def test_builder_creates_base_telemetry_data(self):
        """Test that builder creates BaseTelemetryData."""
        telem = TelemetryDataBuilder().build()
        
        assert isinstance(telem, BaseTelemetryData)
        assert len(telem) > 0
    
    def test_builder_fluent_interface(self):
        """Test that builder methods can be chained."""
        telem = (
            TelemetryDataBuilder()
            .ffb_type("pedals")
            .on_ground()
            .ground_speed(15.0)
            .surface_type("Asphalt")
            .build()
        )
        
        assert telem["FFBType"] == "pedals"
        assert telem["SimOnGround"] == 1
        assert telem["GroundSpeed"] == 15.0
        assert telem["SurfaceType"] == "Asphalt"
    
    def test_builder_autopilot_state(self):
        """Test setting autopilot state."""
        telem = TelemetryDataBuilder().autopilot(True).build()
        
        assert telem["APMaster"] == 1
        assert telem["APServos"] == 1
    
    def test_builder_trim_values(self):
        """Test setting trim values."""
        telem = (
            TelemetryDataBuilder()
            .elevator_trim(0.5)
            .aileron_trim(0.3)
            .rudder_trim(-0.2)
            .build()
        )
        
        assert telem["ElevTrimPct"] == 0.5
        assert telem["AileronTrimPct"] == 0.3
        assert telem["RudderTrimPct"] == -0.2
    
    def test_builder_custom_values(self):
        """Test setting custom telemetry values."""
        telem = (
            TelemetryDataBuilder()
            .set("CustomValue", 42)
            .set("AnotherValue", "test")
            .build()
        )
        
        assert telem["CustomValue"] == 42
        assert telem["AnotherValue"] == "test"


class TestEffectAssertions(BaseTelemetryEffectTestCase):
    """Tests for effect assertion helpers."""
    
    def test_assert_effect_started(self):
        """Test the assert_effect_started helper."""
        effect = self.mock_effects['test']
        
        # Should fail when not started
        with pytest.raises(AssertionError):
            assert_effect_started(effect)
        
        # Should pass when started
        effect.start()
        assert_effect_started(effect)  # Should not raise
    
    def test_assert_friction_coefficient_in_range(self):
        """Test the friction coefficient range assertion."""
        effect = self.mock_effects['test']
        effect.friction(2048, 2048)
        
        # Should pass when in range
        assert_friction_coefficient_in_range(effect, 2000, 2100)
        
        # Should fail when out of range
        with pytest.raises(AssertionError):
            assert_friction_coefficient_in_range(effect, 3000, 4000)


class TestSimulatorMocking(BaseTelemetryEffectTestCase):
    """Tests for simulator type mocking."""
    
    def test_can_mock_msfs(self):
        """Test that we can mock MSFS simulator."""
        instance = self.create_test_instance(MfsfXpSteeringFrictionEffectMixIn)
        instance._test_sim_is_msfs = True
        
        assert instance._sim_is_msfs()
        assert not instance._sim_is_xplane()
    
    def test_can_mock_xplane(self):
        """Test that we can mock X-Plane simulator."""
        instance = self.create_test_instance(MfsfXpSteeringFrictionEffectMixIn)
        instance._test_sim_is_xplane = True
        
        assert not instance._sim_is_msfs()
        assert instance._sim_is_xplane()
    
    def test_ffb_type_checking(self):
        """Test FFB type checking methods."""
        instance = self.create_test_instance(MfsfXpSteeringFrictionEffectMixIn)
        
        # Set as pedals
        instance._telem_data = BaseTelemetryData({"FFBType": "pedals"})
        assert instance.is_pedals()
        assert not instance.is_joystick()
        
        # Set as joystick
        instance._telem_data = BaseTelemetryData({"FFBType": "joystick"})
        assert instance.is_joystick()
        assert not instance.is_pedals()


class TestDirectMethodCalls(BaseTelemetryEffectTestCase):
    """Tests showing how to call effect methods directly."""
    
    def test_direct_method_call(self):
        """Test calling the effect update method directly."""
        instance = self.create_test_instance(MfsfXpSteeringFrictionEffectMixIn)
        instance._test_sim_is_msfs = True
        instance.steering_friction = 1
        instance.enable_friction_ovd = True
        instance.friction_force = 0.2
        
        # Create telemetry
        telem = (
            TelemetryDataBuilder()
            .ffb_type("pedals")
            .on_ground()
            .weight_on_wheels(1.0, 1.0, 1.0)
            .ground_speed(10.0)
            .build()
        )
        self.set_telemetry(instance, telem)
        
        # Call the effect method directly
        instance.msfs_update_steering_friction_effect(telem)
        
        # Verify effect was started
        effect = self.mock_effects['friction']
        assert effect.started
        assert effect.name == "steering_friction"
        assert instance.friction_effect_overridden
    
    def test_effect_respects_speed(self):
        """Test that effect responds to speed changes."""
        instance = self.create_test_instance(MfsfXpSteeringFrictionEffectMixIn)
        instance._test_sim_is_msfs = True
        instance.steering_friction = 1
        instance.enable_friction_ovd = True
        instance.friction_force = 0.0
        instance.steering_friction_intensity = 1.0
        instance.steering_friction_expo = 0.0  # Linear
        
        # Low speed
        telem_low = (
            TelemetryDataBuilder()
            .ffb_type("pedals")
            .on_ground()
            .weight_on_wheels(1.0, 1.0, 1.0)
            .ground_speed(0.0)
            .build()
        )

        self.set_telemetry(instance, telem_low)
        instance.msfs_update_steering_friction_effect(telem_low)
        effect = self.mock_effects['friction']
        low_coeff = effect.get_coefficients()[0]
        
        # High speed
        telem_high = (
            TelemetryDataBuilder()
            .ffb_type("pedals")
            .on_ground()
            .weight_on_wheels(1.0, 1.0, 1.0)
            .ground_speed(20.0)
            .build()
        )
        
        instance.msfs_update_steering_friction_effect(telem_high)
        high_coeff = effect.get_coefficients()[0]
        
        # Friction should decrease with speed
        assert low_coeff > high_coeff, (
            f"Low speed friction ({low_coeff}) should be > high speed ({high_coeff})"
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
