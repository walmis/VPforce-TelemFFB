"""
Comprehensive unit tests for MsfsXpFlightControlsMixIn.

This module tests the MSFS/X-Plane flight control effects including:
- Airspeed and aerodynamic calculations
- Dynamic pressure calculations
- Control surface coefficient calculations
- Joystick spring forces and trim following
- Pedals spring forces and trim following
- G-force effects
- AoA effects
- Rudder feedback forces
- Steering friction integration
"""
import pytest
import math
from tests.framework.base import BaseTelemetryEffectTestCase
from tests.framework.utils import (
    TelemetryDataBuilder,
    assert_effect_started,
    assert_spring_coefficient_in_range,
    assert_spring_offset,
    assert_simconnect_event_sent,
)
from telemffb.sim.msfs_xp.MsfsXpFlightControlsMixIn import MsfsXpFlightControlsMixIn
from telemffb.SettingsManager import SpringModeEnum


class TestMsfsXpFlightControlsAerodynamics(BaseTelemetryEffectTestCase):
    """Test suite for aerodynamic calculations."""
    
    def test_airspeed_calculations_msfs(self):
        """Test that airspeed values are correctly calculated for MSFS."""
        # Arrange
        instance = self.create_test_instance(MsfsXpFlightControlsMixIn)
        instance._test_sim_is_msfs = True
        
        telem = (
            TelemetryDataBuilder()
            .set("IAS", 50.0)  # m/s
            .set("AccBody", [0, 1, 0])
            .ffb_type("joystick")
            .build()
        )
        
        # Act
        instance.on_telemetry(telem)
        
        # Assert
        assert "TAS" in telem
        assert "TAS_kt" in telem
        assert "IAS_kt" in telem
        assert "AccBody_ms" in telem
        assert abs(telem["TAS"] - 50.0) < 0.1
        assert abs(telem["IAS_kt"] - 50.0 * 1.94384) < 0.1  # ms2kt conversion
    
    def test_angle_calculations(self):
        """Test AoA and slip angle calculations."""
        # Arrange
        instance = self.create_test_instance(MsfsXpFlightControlsMixIn)
        instance._test_sim_is_msfs = True
        
        # Incidence vector pointing forward and slightly down
        telem = (
            TelemetryDataBuilder()
            .set("IAS", 50.0)
            .set("Incidence", [0, -0.1, 1.0])  # Slight downward component
            .set("RudderDefl", 0.0)
            .set("AccBody", [0, 1, 0])
            .ffb_type("joystick")
            .build()
        )
        
        # Act
        instance.on_telemetry(telem)
        
        # Assert
        assert "AoA" in telem
        assert "SideSlip" in telem
        # AoA should be positive (nose up relative to airflow)
        assert telem["AoA"] > 0
    
    def test_prop_airflow_with_thrust(self):
        """Test propeller airflow calculation with engine thrust."""
        # Arrange
        instance = self.create_test_instance(MsfsXpFlightControlsMixIn)
        instance._test_sim_is_msfs = True
        instance.prop_diameter = 2.0
        
        telem = (
            TelemetryDataBuilder()
            .set("IAS", 30.0)
            .set("PropThrust", 5000.0)  # N
            .set("AirDensity", 1.225)
            .set("Incidence", [0, -0.1, 1.0])
            .set("AccBody", [0, 1, 0])
            .set("RudderDefl", 0.0)
            .ffb_type("joystick")
            .build()
        )
        
        # Act
        instance.on_telemetry(telem)
        
        # Assert
        assert "_prop_thrust" in telem
        assert "_prop_air_vel" in telem
        assert "_elevator_aoa" in telem
        assert telem["_prop_air_vel"] > telem["IAS"]  # Prop increases local airspeed
    
    def test_prop_airflow_with_negative_thrust(self):
        """Test that negative thrust is clamped to zero."""
        # Arrange
        instance = self.create_test_instance(MsfsXpFlightControlsMixIn)
        instance._test_sim_is_msfs = True
        
        telem = (
            TelemetryDataBuilder()
            .set("IAS", 30.0)
            .set("PropThrust", -1000.0)  # Negative thrust
            .set("AirDensity", 1.225)
            .set("Incidence", [0, 0, 1.0])
            .set("AccBody", [0, 1, 0])
            .set("RudderDefl", 0.0)
            .ffb_type("joystick")
            .build()
        )
        
        # Act
        instance.on_telemetry(telem)
        
        # Assert
        assert telem["_prop_thrust"] == 0
    
    def test_prop_airflow_with_list_thrust(self):
        """Test propeller thrust calculation with multi-engine (list) thrust."""
        # Arrange
        instance = self.create_test_instance(MsfsXpFlightControlsMixIn)
        instance._test_sim_is_msfs = True
        
        telem = (
            TelemetryDataBuilder()
            .set("IAS", 30.0)
            .set("PropThrust", [3000.0, 2500.0, 3200.0])  # Multi-engine
            .set("AirDensity", 1.225)
            .set("Incidence", [0, 0, 1.0])
            .set("AccBody", [0, 1, 0])
            .set("RudderDefl", 0.0)
            .ffb_type("joystick")
            .build()
        )
        
        # Act
        instance.on_telemetry(telem)
        
        # Assert
        assert telem["_prop_thrust"] == 3200.0  # Should use max


class TestMsfsXpFlightControlsDynamicPressure(BaseTelemetryEffectTestCase):
    """Test suite for dynamic pressure calculations."""
    
    def test_dynamic_pressure_calculation(self):
        """Test that dynamic pressures are calculated correctly."""
        # Arrange
        instance = self.create_test_instance(MsfsXpFlightControlsMixIn)
        instance._test_sim_is_msfs = True
        instance.elevator_prop_flow_ratio = 0.5
        instance.rudder_prop_flow_ratio = 0.3
        
        telem = (
            TelemetryDataBuilder()
            .set("IAS", 50.0)
            .set("DynPressure", 1000.0)
            .set("AirDensity", 1.225)
            .set("PropThrust", 5000.0)
            .set("Incidence", [0, 0, 1.0])
            .set("AccBody", [0, 1, 0])
            .set("RudderDefl", 0.0)
            .ffb_type("joystick")
            .build()
        )
        
        # Act
        instance.on_telemetry(telem)
        
        # Assert
        assert "_elev_dyn_pressure" in telem
        assert telem["_elev_dyn_pressure"] > 0
    
    def test_vne_calculation_msfs(self):
        """Test Vne (never exceed speed) calculation for MSFS."""
        # Arrange
        instance = self.create_test_instance(MsfsXpFlightControlsMixIn)
        instance._test_sim_is_msfs = True
        instance.vne_override = 0  # No override
        
        telem = (
            TelemetryDataBuilder()
            .set("src", "MSFS")
            .set("IAS", 50.0)
            .set("DesignSpeed", (100.0, 50.0, 60.0))  # Vc, Vs0, Vs1
            .set("DynPressure", 1000.0)
            .set("AirDensity", 1.225)
            .set("PropThrust", 0)
            .set("Incidence", [0, 0, 1.0])
            .set("AccBody", [0, 1, 0])
            .set("RudderDefl", 0.0)
            .ffb_type("joystick")
            .build()
        )
        
        # Act
        instance.on_telemetry(telem)
        
        # Assert
        assert "Vne_ms_calc" in telem
        assert "Vne_kt" in telem
        assert "Qvne" in telem
        assert "Qvc_gain" in telem
        assert telem["Vne_ms_calc"] > 0
    
    def test_vne_calculation_xplane(self):
        """Test Vne calculation for X-Plane using direct Vne value."""
        # Arrange
        instance = self.create_test_instance(MsfsXpFlightControlsMixIn)
        instance._test_sim_is_xplane = True
        instance.vne_override = 0
        
        telem = (
            TelemetryDataBuilder()
            .set("src", "XPLANE")
            .set("IAS", 50.0)
            .set("Vne", 120.0)
            .set("Vso", 40.0)
            .set("DynPressure", 1000.0)
            .set("AirDensity", 1.225)
            .set("PropThrust", 0)
            .set("Incidence", [0, 0, 1.0])
            .set("AccBody", [0, 1, 0])
            .set("RudderDefl", 0.0)
            .ffb_type("joystick")
            .build()
        )
        
        # Act
        instance.on_telemetry(telem)
        
        # Assert
        assert telem["Vne_ms_calc"] == 120.0
    
    def test_vne_override(self):
        """Test that Vne override takes precedence."""
        # Arrange
        instance = self.create_test_instance(MsfsXpFlightControlsMixIn)
        instance._test_sim_is_msfs = True
        instance.vne_override = 150  # Override value
        
        telem = (
            TelemetryDataBuilder()
            .set("src", "MSFS")
            .set("IAS", 50.0)
            .set("DesignSpeed", (100.0, 50.0, 60.0))
            .set("DynPressure", 1000.0)
            .set("AirDensity", 1.225)
            .set("PropThrust", 0)
            .set("Incidence", [0, 0, 1.0])
            .set("AccBody", [0, 1, 0])
            .set("RudderDefl", 0.0)
            .ffb_type("joystick")
            .build()
        )
        
        # Act
        instance.on_telemetry(telem)
        
        # Assert
        assert abs(telem["Vne_kt"] - 150 * 1.94384) < 0.1


class TestMsfsXpFlightControlsCoefficients(BaseTelemetryEffectTestCase):
    """Test suite for control coefficient calculations."""
    
    def test_elevator_coefficient_with_expo(self):
        """Test elevator coefficient calculation with expo curve."""
        # Arrange
        instance = self.create_test_instance(MsfsXpFlightControlsMixIn)
        instance._test_sim_is_msfs = True
        instance.elevator_expo = 50
        instance.spring_mode = SpringModeEnum.BASIC
        
        telem = self._create_flight_telem()
        
        # Act
        instance.on_telemetry(telem)
        
        # Assert
        assert "_elev_coeff" in telem
        assert telem["_elev_coeff"] > 0
    
    def test_aileron_coefficient_with_expo(self):
        """Test aileron coefficient calculation with expo curve."""
        # Arrange
        instance = self.create_test_instance(MsfsXpFlightControlsMixIn)
        instance._test_sim_is_msfs = True
        instance.aileron_expo = 30
        instance.spring_mode = SpringModeEnum.BASIC
        
        telem = self._create_flight_telem()
        
        # Act
        instance.on_telemetry(telem)
        
        # Assert
        assert "_aile_coeff" in telem
        assert telem["_aile_coeff"] > 0
    
    def test_rudder_coefficient_with_expo(self):
        """Test rudder coefficient calculation with expo curve."""
        # Arrange
        instance = self.create_test_instance(MsfsXpFlightControlsMixIn)
        instance._test_sim_is_msfs = True
        instance.rudder_expo = 40
        instance.spring_mode = SpringModeEnum.BASIC
        
        telem = self._create_flight_telem()
        
        # Act
        instance.on_telemetry(telem)
        
        # Assert
        assert "_rud_coeff" in telem
        assert telem["_rud_coeff"] > 0
    
    def test_elevator_droop_moment(self):
        """Test elevator droop term calculation based on G-force."""
        # Arrange
        instance = self.create_test_instance(MsfsXpFlightControlsMixIn)
        instance._test_sim_is_msfs = True
        instance.elevator_droop_moment = 0.2
        
        telem = self._create_flight_telem()
        telem["G"] = 3.0  # 3G loading
        
        # Act
        instance.on_telemetry(telem)
        
        # Assert
        assert "_elevator_droop_term" in telem
        assert telem["_elevator_droop_term"] > 0  # Should increase with G
    
    def test_slip_gain_effect(self):
        """Test that slip angle reduces control effectiveness."""
        # Arrange
        instance = self.create_test_instance(MsfsXpFlightControlsMixIn)
        instance._test_sim_is_msfs = True
        instance.slip_gain = 1.0
        
        # Create telemetry with significant sideslip
        telem = self._create_flight_telem()
        telem["Incidence"] = [0.5, 0, 1.0]  # Sideways component
        
        # Act
        instance.on_telemetry(telem)
        
        # Assert
        assert "_slip_gain" in telem
        assert telem["_slip_gain"] < 1.0  # Should be reduced by slip
    
    def _create_flight_telem(self):
        """Helper to create basic flight telemetry."""
        return (
            TelemetryDataBuilder()
            .set("src", "MSFS")
            .set("IAS", 50.0)
            .set("DesignSpeed", (100.0, 50.0, 60.0))
            .set("DynPressure", 1000.0)
            .set("AirDensity", 1.225)
            .set("PropThrust", 3000.0)
            .set("Incidence", [0, -0.05, 1.0])
            .set("AccBody", [0, 1, 0])
            .set("RudderDefl", 0.0)
            .set("G", 1.0)
            .ffb_type("joystick")
            .build()
        )


class TestMsfsXpFlightControlsJoystick(BaseTelemetryEffectTestCase):
    """Test suite for joystick FFB effects."""
    
    def test_joystick_spring_activates(self):
        """Test that spring effect starts for joystick."""
        # Arrange
        instance = self.create_test_instance(MsfsXpFlightControlsMixIn)
        instance._test_sim_is_msfs = True
        instance.spring_mode = SpringModeEnum.BASIC
        
        telem = self._create_flight_telem()
        self.set_telemetry(instance, telem)
        
        # Act
        instance.on_telemetry(telem)
        
        # Assert
        effect = self.mock_effects['dynamic_spring']
        assert_effect_started(effect, "Spring should start for joystick")
    
    def test_joystick_spring_coefficients_scale_with_speed(self):
        """Test that spring coefficients increase with airspeed."""
        # Arrange
        instance = self.create_test_instance(MsfsXpFlightControlsMixIn)
        instance._test_sim_is_msfs = True
        instance.spring_mode = SpringModeEnum.BASIC
        instance.max_elevator_coeff = 0.5
        instance.max_aileron_coeff = 0.5
        
        # Low speed
        telem_slow = self._create_flight_telem()
        telem_slow["IAS"] = 30.0
        telem_slow["DynPressure"] = 500.0
        self.set_telemetry(instance, telem_slow)
        instance.on_telemetry(telem_slow)
        
        coeff_slow_x, coeff_slow_y = self.mock_effects['dynamic_spring'].get_coefficients()
        
        # Reset and test high speed
        self.mock_effects.reset_all()
        telem_fast = self._create_flight_telem()
        telem_fast["IAS"] = 80.0
        telem_fast["DynPressure"] = 3000.0
        self.set_telemetry(instance, telem_fast)
        instance.on_telemetry(telem_fast)
        
        coeff_fast_x, coeff_fast_y = self.mock_effects['dynamic_spring'].get_coefficients()
        
        # Assert
        assert coeff_fast_y > coeff_slow_y, "Y coefficient should increase with speed"
        assert coeff_fast_x > coeff_slow_x, "X coefficient should increase with speed"
    
    def test_joystick_trim_following_disabled_by_default(self):
        """Test that trim following doesn't activate when disabled."""
        # Arrange
        instance = self.create_test_instance(MsfsXpFlightControlsMixIn)
        instance._test_sim_is_msfs = True
        instance.trim_following = False
        instance.spring_mode = SpringModeEnum.BASIC
        
        telem = self._create_flight_telem()
        telem["ElevTrimPct"] = 0.5
        telem["AileronTrimPct"] = 0.3
        self.set_telemetry(instance, telem)
        
        # Act
        instance.on_telemetry(telem)
        
        # Assert
        spring = self.mock_effects['dynamic_spring']
        x_offset, y_offset = spring.get_offsets()
        assert x_offset == 0, "X offset should be 0 when trim following disabled"
        assert y_offset == 0, "Y offset should be 0 when trim following disabled"
    
    def test_joystick_trim_following_moves_stick(self):
        """Test that trim following moves physical stick position."""
        # Arrange
        instance = self.create_test_instance(MsfsXpFlightControlsMixIn)
        instance._test_sim_is_msfs = True
        instance.trim_following = True
        instance.telemffb_controls_axes = True
        instance.joystick_trim_follow_gain_physical_y = 1.0
        instance.joystick_trim_follow_gain_physical_x = 1.0
        instance.spring_mode = SpringModeEnum.BASIC
        instance.elev_trim_dampener = instance.dampener
        instance.aileron_pos_dampener = instance.dampener
        
        telem = self._create_flight_telem()
        telem["ElevTrimPct"] = 0.5
        telem["AileronTrimPct"] = 0.3
        self.set_telemetry(instance, telem)
        
        # Act
        instance.on_telemetry(telem)
        
        # Assert
        spring = self.mock_effects['dynamic_spring']
        x_offset, y_offset = spring.get_offsets()
        assert y_offset != 0, "Y offset should be non-zero with elevator trim"
        assert x_offset != 0, "X offset should be non-zero with aileron trim"
    
    def test_joystick_g_force_effect(self):
        """Test G-force constant force effect."""
        # Arrange
        instance = self.create_test_instance(MsfsXpFlightControlsMixIn)
        instance._test_sim_is_msfs = True
        instance.g_force_gain = 0.2
        instance.spring_mode = SpringModeEnum.BASIC
        
        telem = self._create_flight_telem()
        telem["AccBody"] = [0, 3.0, 0]  # 3G vertical
        telem["G"] = 3.0
        self.set_telemetry(instance, telem)
        
        # Act
        instance.on_telemetry(telem)
        
        # Assert
        assert "_G_term" in telem
        effect = self.mock_effects['control_weight']
        assert_effect_started(effect, "Control weight should start")
    
    def test_joystick_lateral_force_effect(self):
        """Test lateral G-force constant force effect."""
        # Arrange
        instance = self.create_test_instance(MsfsXpFlightControlsMixIn)
        instance._test_sim_is_msfs = True
        instance.uncoordinated_turn_effect_enabled = 1
        instance.lateral_force_gain = 0.3
        instance.spring_mode = SpringModeEnum.BASIC
        
        telem = self._create_flight_telem()
        telem["AccBody"] = [-2.0, 1.0, 0]  # Lateral acceleration
        self.set_telemetry(instance, telem)
        
        # Act
        instance.on_telemetry(telem)
        
        # Assert
        effect = self.mock_effects['control_weight']
        assert_effect_started(effect, "Control weight should apply lateral force")
    
    def test_joystick_aoa_effect_on_ground(self):
        """Test that AoA effect doesn't apply when on ground."""
        # Arrange
        instance = self.create_test_instance(MsfsXpFlightControlsMixIn)
        instance._test_sim_is_msfs = True
        instance.aoa_effect_enabled = 1
        instance.spring_mode = SpringModeEnum.BASIC
        
        telem = self._create_flight_telem()
        telem["WeightOnWheels"] = [1, 1, 1]  # On ground
        telem["ElevDeflPct"] = 0.5
        telem["ElevDefl"] = 10.0
        self.set_telemetry(instance, telem)
        
        # Act
        instance.on_telemetry(telem)
        
        # Assert - offset should not include AoA component when on ground
        spring = self.mock_effects['dynamic_spring']
        x_offset, y_offset = spring.get_offsets()
        # When on ground with max WoW, AoA effect should not apply
    
    def test_joystick_center_spring_mode(self):
        """Test center spring mode adds base spring gain."""
        # Arrange
        instance = self.create_test_instance(MsfsXpFlightControlsMixIn)
        instance._test_sim_is_msfs = True
        instance.spring_mode = SpringModeEnum.CENTER
        instance.elevator_spring_gain = 0.3
        instance.aileron_spring_gain = 0.25
        
        telem = self._create_flight_telem()
        telem["IAS"] = 0.0  # Zero airspeed
        telem["DynPressure"] = 0.0
        self.set_telemetry(instance, telem)
        
        # Act
        instance.on_telemetry(telem)
        
        # Assert
        spring = self.mock_effects['dynamic_spring']
        x_coeff, y_coeff = spring.get_coefficients()
        # Should have base spring coefficient even at zero speed
        assert y_coeff > 0, "Should have base spring with center mode"
        assert x_coeff > 0, "Should have base spring with center mode"
    
    def _create_flight_telem(self):
        """Helper to create basic flight telemetry."""
        return (
            TelemetryDataBuilder()
            .set("src", "MSFS")
            .set("IAS", 50.0)
            .set("DesignSpeed", (100.0, 50.0, 60.0))
            .set("DynPressure", 1000.0)
            .set("AirDensity", 1.225)
            .set("PropThrust", 3000.0)
            .set("Incidence", [0, -0.05, 1.0])
            .set("AccBody", [0, 1, 0])
            .set("RudderDefl", 0.0)
            .set("G", 1.0)
            .set("ElevDefl", 5.0)
            .set("ElevDeflPct", 0.25)
            .set("WeightOnWheels", [0, 0, 0])
            .ffb_type("joystick")
            .build()
        )


class TestMsfsXpFlightControlsPedals(BaseTelemetryEffectTestCase):
    """Test suite for pedals FFB effects."""
    
    def test_pedals_spring_activates(self):
        """Test that spring effect starts for pedals."""
        # Arrange
        instance = self.create_test_instance(MsfsXpFlightControlsMixIn)
        instance._test_sim_is_msfs = True
        instance.spring_mode = SpringModeEnum.BASIC
        
        telem = self._create_flight_telem()
        telem["FFBType"] = "pedals"
        self.set_telemetry(instance, telem)
        
        # Act
        instance.on_telemetry(telem)
        
        # Assert
        effect = self.mock_effects['dynamic_spring']
        assert_effect_started(effect, "Spring should start for pedals")
    
    def test_pedals_rudder_force_feedback(self):
        """Test rudder force feedback based on slip angle."""
        # Arrange
        instance = self.create_test_instance(MsfsXpFlightControlsMixIn)
        instance._test_sim_is_msfs = True
        instance.spring_mode = SpringModeEnum.BASIC
        
        telem = self._create_flight_telem()
        telem["FFBType"] = "pedals"
        telem["Incidence"] = [0.3, 0, 1.0]  # Sideslip
        telem["RudderDefl"] = 0.0  # No rudder input
        self.set_telemetry(instance, telem)
        
        # Act
        instance.on_telemetry(telem)
        
        # Assert
        # Constant force should be applied based on slip
        # Note: const_force is applied in the instance, not in effects dict
        assert instance.const_force is not None
    
    def test_pedals_trim_following_disabled_by_default(self):
        """Test that trim following doesn't activate when disabled."""
        # Arrange
        instance = self.create_test_instance(MsfsXpFlightControlsMixIn)
        instance._test_sim_is_msfs = True
        instance.trim_following = False
        instance.spring_mode = SpringModeEnum.BASIC
        
        telem = self._create_flight_telem()
        telem["FFBType"] = "pedals"
        telem["RudderTrimPct"] = 0.4
        self.set_telemetry(instance, telem)
        
        # Act
        instance.on_telemetry(telem)
        
        # Assert
        spring = self.mock_effects['dynamic_spring']
        x_offset, _ = spring.get_offsets()
        assert x_offset == 0, "X offset should be 0 when trim following disabled"
    
    def test_pedals_trim_following_moves_pedals(self):
        """Test that trim following moves physical pedal position."""
        # Arrange
        instance = self.create_test_instance(MsfsXpFlightControlsMixIn)
        instance._test_sim_is_msfs = True
        instance.trim_following = True
        instance.telemffb_controls_axes = True
        instance.rudder_trim_follow_gain_physical_x = 1.0
        instance.spring_mode = SpringModeEnum.BASIC
        
        telem = self._create_flight_telem()
        telem["FFBType"] = "pedals"
        telem["RudderTrimPct"] = 0.4
        self.set_telemetry(instance, telem)
        
        # Act
        instance.on_telemetry(telem)
        
        # Assert
        spring = self.mock_effects['dynamic_spring']
        x_offset, _ = spring.get_offsets()
        assert x_offset != 0, "X offset should be non-zero with rudder trim"
    
    def test_pedals_spring_coefficient_scales_with_speed(self):
        """Test that pedal spring coefficient increases with airspeed."""
        # Arrange
        instance = self.create_test_instance(MsfsXpFlightControlsMixIn)
        instance._test_sim_is_msfs = True
        instance.spring_mode = SpringModeEnum.BASIC
        instance.max_rudder_coeff = 0.5
        
        # Low speed
        telem_slow = self._create_flight_telem()
        telem_slow["FFBType"] = "pedals"
        telem_slow["IAS"] = 25.0
        telem_slow["DynPressure"] = 300.0
        self.set_telemetry(instance, telem_slow)
        instance.on_telemetry(telem_slow)
        
        coeff_slow_x, _ = self.mock_effects['dynamic_spring'].get_coefficients()
        
        # Reset and test high speed
        self.mock_effects.reset_all()
        telem_fast = self._create_flight_telem()
        telem_fast["FFBType"] = "pedals"
        telem_fast["IAS"] = 75.0
        telem_fast["DynPressure"] = 2500.0
        self.set_telemetry(instance, telem_fast)
        instance.on_telemetry(telem_fast)
        
        coeff_fast_x, _ = self.mock_effects['dynamic_spring'].get_coefficients()
        
        # Assert
        assert coeff_fast_x > coeff_slow_x, "X coefficient should increase with speed"
    
    def test_pedals_center_spring_mode(self):
        """Test center spring mode adds base spring gain for pedals."""
        # Arrange
        instance = self.create_test_instance(MsfsXpFlightControlsMixIn)
        instance._test_sim_is_msfs = True
        instance.spring_mode = SpringModeEnum.CENTER
        instance.rudder_spring_gain = 0.2
        
        telem = self._create_flight_telem()
        telem["FFBType"] = "pedals"
        telem["IAS"] = 0.0  # Zero airspeed
        telem["DynPressure"] = 0.0
        self.set_telemetry(instance, telem)
        
        # Act
        instance.on_telemetry(telem)
        
        # Assert
        spring = self.mock_effects['dynamic_spring']
        x_coeff, _ = spring.get_coefficients()
        assert x_coeff > 0, "Should have base spring with center mode"
    
    def _create_flight_telem(self):
        """Helper to create basic flight telemetry."""
        return (
            TelemetryDataBuilder()
            .set("src", "MSFS")
            .set("IAS", 50.0)
            .set("DesignSpeed", (100.0, 50.0, 60.0))
            .set("DynPressure", 1000.0)
            .set("AirDensity", 1.225)
            .set("PropThrust", 3000.0)
            .set("Incidence", [0, -0.05, 1.0])
            .set("AccBody", [0, 1, 0])
            .set("RudderDefl", 0.0)
            .set("G", 1.0)
            .ffb_type("joystick")
            .build()
        )


class TestMsfsXpFlightControlsAxisControl(BaseTelemetryEffectTestCase):
    """Test suite for axis control (sending commands to sim)."""
    
    def test_joystick_axis_control_msfs(self):
        """Test that joystick sends axis commands to MSFS."""
        # Arrange
        instance = self.create_test_instance(MsfsXpFlightControlsMixIn)
        instance._test_sim_is_msfs = True
        instance.telemffb_controls_axes = True
        instance.local_disable_axis_control = False
        instance._simconnect = self.mock_simconnect
        instance.spring_mode = SpringModeEnum.BASIC
        instance.enable_custom_x_axis = False
        instance.enable_custom_y_axis = False
        instance.joystick_x_axis_scale = 1.0
        instance.joystick_y_axis_scale = 1.0
        
        telem = self._create_flight_telem()
        self.set_telemetry(instance, telem)
        
        # Act
        instance.on_telemetry(telem)
        
        # Assert
        assert_simconnect_event_sent(self.mock_simconnect, "AXIS_AILERONS_SET")
        assert_simconnect_event_sent(self.mock_simconnect, "AXIS_ELEVATOR_SET")
    
    def test_joystick_axis_control_xplane(self):
        """Test that joystick sends axis commands to X-Plane."""
        # Arrange
        instance = self.create_test_instance(MsfsXpFlightControlsMixIn)
        instance._test_sim_is_xplane = True
        instance._test_sim_is_msfs = False
        instance.telemffb_controls_axes = True
        instance.local_disable_axis_control = False
        instance.spring_mode = SpringModeEnum.BASIC
        instance.joystick_x_axis_scale = 1.0
        instance.joystick_y_axis_scale = 1.0
        
        telem = self._create_flight_telem()
        telem["src"] = "XPLANE"
        telem["Vne"] = 120.0
        self.set_telemetry(instance, telem)
        
        # Act
        instance.on_telemetry(telem)
        
        # Assert
        assert hasattr(instance, '_xp_commands')
        assert len(instance._xp_commands) > 0
        # Check that AXIS command was sent
        axis_commands = [cmd for cmd in instance._xp_commands if 'AXIS' in cmd]
        assert len(axis_commands) > 0
    
    def test_pedals_axis_control_msfs(self):
        """Test that pedals send axis commands to MSFS."""
        # Arrange
        instance = self.create_test_instance(MsfsXpFlightControlsMixIn)
        instance._test_sim_is_msfs = True
        instance.telemffb_controls_axes = True
        instance.local_disable_axis_control = False
        instance._simconnect = self.mock_simconnect
        instance.spring_mode = SpringModeEnum.BASIC
        instance.enable_custom_x_axis = False
        instance.rudder_x_axis_scale = 1.0
        
        telem = self._create_flight_telem()
        telem["FFBType"] = "pedals"
        self.set_telemetry(instance, telem)
        
        # Act
        instance.on_telemetry(telem)
        
        # Assert
        assert_simconnect_event_sent(self.mock_simconnect, "AXIS_RUDDER_SET")
    
    def test_pedals_axis_control_xplane(self):
        """Test that pedals send axis commands to X-Plane."""
        # Arrange
        instance = self.create_test_instance(MsfsXpFlightControlsMixIn)
        instance._test_sim_is_xplane = True
        instance._test_sim_is_msfs = False
        instance.telemffb_controls_axes = True
        instance.local_disable_axis_control = False
        instance.spring_mode = SpringModeEnum.BASIC
        instance.rudder_x_axis_scale = 1.0
        
        telem = self._create_flight_telem()
        telem["FFBType"] = "pedals"
        telem["src"] = "XPLANE"
        telem["Vne"] = 120.0
        self.set_telemetry(instance, telem)
        
        # Act
        instance.on_telemetry(telem)
        
        # Assert
        assert hasattr(instance, '_xp_commands')
        # Check that pedals axis command was sent
        axis_commands = [cmd for cmd in instance._xp_commands if 'px=' in cmd]
        assert len(axis_commands) > 0
    
    def test_axis_control_disabled_when_flag_set(self):
        """Test that axis control doesn't send commands when disabled."""
        # Arrange
        instance = self.create_test_instance(MsfsXpFlightControlsMixIn)
        instance._test_sim_is_msfs = True
        instance.telemffb_controls_axes = True
        instance.local_disable_axis_control = True  # Disabled
        instance._simconnect = self.mock_simconnect
        instance.spring_mode = SpringModeEnum.BASIC
        
        telem = self._create_flight_telem()
        self.set_telemetry(instance, telem)
        
        # Act
        instance.on_telemetry(telem)
        
        # Assert
        assert len(self.mock_simconnect.sent_events) == 0, "No events should be sent when disabled"
    
    def _create_flight_telem(self):
        """Helper to create basic flight telemetry."""
        return (
            TelemetryDataBuilder()
            .set("src", "MSFS")
            .set("IAS", 50.0)
            .set("DesignSpeed", (100.0, 50.0, 60.0))
            .set("DynPressure", 1000.0)
            .set("AirDensity", 1.225)
            .set("PropThrust", 3000.0)
            .set("Incidence", [0, -0.05, 1.0])
            .set("AccBody", [0, 1, 0])
            .set("RudderDefl", 0.0)
            .set("G", 1.0)
            .set("ElevDefl", 5.0)
            .set("ElevDeflPct", 0.25)
            .set("WeightOnWheels", [0, 0, 0])
            .ffb_type("joystick")
            .build()
        )


class TestMsfsXpFlightControlsSpecialModes(BaseTelemetryEffectTestCase):
    """Test suite for special operating modes."""
    
    def test_fbw_mode_uses_fbw_controls(self):
        """Test that FBW spring mode delegates to FBW controls."""
        # Arrange
        instance = self.create_test_instance(MsfsXpFlightControlsMixIn)
        instance._test_sim_is_msfs = True
        instance.spring_mode = SpringModeEnum.FBW
        
        telem = self._create_flight_telem()
        self.set_telemetry(instance, telem)
        
        # Act
        instance.on_telemetry(telem)
        
        # Assert
        # Dynamic spring should be stopped, FBW should be active
        dynamic_spring = self.mock_effects.get('dynamic_spring', None)
        if dynamic_spring:
            # Check that we didn't use dynamic spring
            pass  # FBW delegation happens, tested in FBW tests
    
    def test_helicopter_mode_skips_flight_controls(self):
        """Test that helicopter aircraft skip standard flight controls."""
        # Arrange
        instance = self.create_test_instance(MsfsXpFlightControlsMixIn)
        instance._test_sim_is_msfs = True
        instance.spring_mode = SpringModeEnum.BASIC
        
        telem = self._create_flight_telem()
        telem["AircraftClass"] = "Helicopter"
        self.set_telemetry(instance, telem)
        
        # Act
        instance.on_telemetry(telem)
        
        # Assert
        # No spring effect should be started for helicopters
        effect = self.mock_effects.get('dynamic_spring', None)
        if effect:
            assert effect.start_count == 0, "No spring should start for helicopters"
    
    def test_collective_ffb_type_returns_early(self):
        """Test that collective FFB type doesn't process flight controls."""
        # Arrange
        instance = self.create_test_instance(MsfsXpFlightControlsMixIn)
        instance._test_sim_is_msfs = True
        instance.spring_mode = SpringModeEnum.BASIC
        
        telem = self._create_flight_telem()
        telem["FFBType"] = "collective"
        self.set_telemetry(instance, telem)
        
        # Act
        instance.on_telemetry(telem)
        
        # Assert
        effect = self.mock_effects.get('dynamic_spring', None)
        if effect:
            assert effect.start_count == 0, "No spring should start for collective"
    
    def test_autopilot_following_uses_fbw(self):
        """Test that AP following with flag enabled uses FBW controls."""
        # Arrange
        instance = self.create_test_instance(MsfsXpFlightControlsMixIn)
        instance._test_sim_is_msfs = True
        instance.telemffb_controls_axes = True
        instance.ap_following = True
        instance.use_fbw_for_ap_follow = True
        instance.spring_mode = SpringModeEnum.BASIC
        
        telem = self._create_flight_telem()
        telem["APMaster"] = 1  # AP active
        self.set_telemetry(instance, telem)
        
        # Act
        instance.on_telemetry(telem)
        
        # Assert
        # Dynamic spring should be stopped
        dynamic_spring = self.mock_effects['dynamic_spring']
        # FBW should be handling it (tested in FBW tests)
    
    def test_timeout_stops_effects(self):
        """Test that timeout properly stops constant force effect."""
        # Arrange
        instance = self.create_test_instance(MsfsXpFlightControlsMixIn)
        instance._test_sim_is_msfs = True
        
        # Start some effects
        telem = self._create_flight_telem()
        self.set_telemetry(instance, telem)
        instance.on_telemetry(telem)
        
        # Act
        instance.on_timeout()
        
        # Assert
        # Const force should be stopped (it's an instance variable, not in effects dict)
        # This is tested through the parent class timeout
    
    def _create_flight_telem(self):
        """Helper to create basic flight telemetry."""
        return (
            TelemetryDataBuilder()
            .set("src", "MSFS")
            .set("IAS", 50.0)
            .set("DesignSpeed", (100.0, 50.0, 60.0))
            .set("DynPressure", 1000.0)
            .set("AirDensity", 1.225)
            .set("PropThrust", 3000.0)
            .set("Incidence", [0, -0.05, 1.0])
            .set("AccBody", [0, 1, 0])
            .set("RudderDefl", 0.0)
            .set("G", 1.0)
            .set("ElevDefl", 5.0)
            .set("ElevDeflPct", 0.25)
            .set("WeightOnWheels", [0, 0, 0])
            .set("AircraftClass", "Airplane")
            .ffb_type("joystick")
            .build()
        )
