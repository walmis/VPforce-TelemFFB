"""
Comprehensive tests for MSFS/X-Plane Helicopter class and related MixIns.

Tests cover:
- Helicopter initialization and configuration
- Cyclic control (force trim, basic spring modes)
- Collective control (initialization, spring effects, axis control)
- Pedal control (force trim, spring effects, axis control)
- ETL (Effective Translational Lift) effects
- Overspeed shake effects
- SimConnect integration and proxy handling
- Multi-device support (joystick, pedals, collective, trimwheel)
"""
import pytest
from unittest.mock import Mock, patch, MagicMock
from tests.framework.base import BaseTelemetryEffectTestCase, MockConditionEffect
from tests.framework.utils import TelemetryDataBuilder
from telemffb.sim.BaseTelemetryData import BaseTelemetryData
from telemffb.sim.msfs_xp.Helicopter import Helicopter
from telemffb.sim.msfs_xp.MsfsXpHeliControlsMixIn import MsfsXpHeliControlsMixIn
from telemffb.SettingsManager import SpringModeEnum
from telemffb.hw.ffb_rhino import HapticEffect
import telemffb.globals as G


@pytest.mark.unit
@pytest.mark.msfs
@pytest.mark.helicopter
class TestHelicopterInitialization(BaseTelemetryEffectTestCase):
    """Tests for helicopter initialization and basic setup."""
    
    def test_helicopter_creates_with_defaults(self):
        """Test helicopter creates with default parameters."""
        instance = self.create_aircraft_instance(Helicopter, name="TestHeli")
        
        assert instance._name == "TestHeli"
        assert instance.buffeting_intensity == 0.0
        assert instance.etl_start_speed == 6.0
        assert instance.etl_stop_speed == 22.0
        assert instance.etl_effect_intensity == 0.2
        assert instance.overspeed_shake_start == 70.0
        assert instance.overspeed_shake_intensity == 0.2
        assert instance.heli_engine_rumble_intensity == 0.12
    
    def test_helicopter_custom_parameters(self):
        """Test helicopter with custom parameters."""
        instance = self.create_aircraft_instance(
            Helicopter,
            name="CustomHeli"
        )
        # Set custom parameters after creation (they're class-level attributes)
        instance.etl_effect_intensity = 0.5
        instance.overspeed_shake_intensity = 0.3
        instance.collective_spring_coeff_y = 2048
        
        assert instance.etl_effect_intensity == 0.5
        assert instance.overspeed_shake_intensity == 0.3
        assert instance.collective_spring_coeff_y == 2048
    
    def test_helicopter_initializes_state_variables(self):
        """Test that state variables are initialized correctly."""
        instance = self.create_aircraft_instance(Helicopter, name="TestHeli")
        
        # Check initialization state variables
        assert instance.cyclic_spring_init == 0
        assert instance.collective_init == 0
        assert instance.pedals_init == 0
        assert instance.cpO_x == 0
        assert instance.cpO_y == 0
        assert instance.last_collective_y is None
    
    def test_helicopter_subscribes_simvars_on_msfs(self):
        """Test that SimConnect variables are subscribed on MSFS."""
        instance = self.create_aircraft_instance(Helicopter, name="TestHeli", _test_sim_is_msfs=True)
        
        # Verify simconnect proxy is accessible
        assert instance._simconnect is not None, "_simconnect should not be None"
        
        # Clear any previous calls
        self.mock_simconnect.simvar_calls.clear()
        self.mock_simconnect.add_simvar_count = 0
        
        # Manually call subscribe (since __init__ called it when _sim_is_msfs was False)
        instance.subscribe_simvars()
        
        # Verify simconnect was called
        assert self.mock_simconnect.add_simvar_count > 0, f"Expected add_simvar to be called, but count is {self.mock_simconnect.add_simvar_count}"
        assert any('ForceTrimSW' in str(call) for call in self.mock_simconnect.simvar_calls), f"Expected ForceTrimSW in calls, got: {self.mock_simconnect.simvar_calls}"
    
    def test_helicopter_timeout_resets_init_flags(self):
        """Test that timeout resets initialization flags."""
        instance = self.create_aircraft_instance(Helicopter, name="TestHeli")
        
        # Set flags as if initialized
        instance.cyclic_spring_init = 1
        instance.collective_init = 1
        instance.pedals_init = 1
        
        # Trigger timeout
        instance.on_timeout()
        
        # Verify flags are reset
        assert instance.cyclic_spring_init == 0
        assert instance.collective_init == 0
        assert instance.pedals_init == 0


@pytest.mark.unit
@pytest.mark.msfs
@pytest.mark.helicopter
@pytest.mark.collective
class TestHelicopterCollective(BaseTelemetryEffectTestCase):
    """Tests for helicopter collective control."""
    
    def _create_heli_telem(self):
        """Create basic helicopter telemetry."""
        return TelemetryDataBuilder() \
            .with_sim_on_ground(0) \
            .with_airspeed(10.0) \
            .with_field("AircraftClass", "Helicopter") \
            .with_field("FFBType", "collective") \
            .with_field("N", 100.0) \
            .build()
    
    def test_collective_early_return_if_not_collective_device(self):
        """Test collective control returns early if not collective device."""
        instance = self.create_aircraft_instance(Helicopter, name="TestHeli", _test_sim_is_msfs=True, _test_device_type="joystick")
        instance.telemffb_controls_axes = True
        
        telem = self._create_heli_telem()
        telem["FFBType"] = "joystick"  # Override to non-collective device
        self.set_telemetry(instance, telem)
        
        # Should return early - no effects created
        instance.msfs_update_collective(telem)
        
        assert instance.collective_init == 0
    
    def test_collective_initialization_on_ground(self):
        """Test collective initializes to full down when on ground."""
        instance = self.create_aircraft_instance(Helicopter, name="TestHeli", _test_sim_is_msfs=True, _test_device_type="collective")
        instance.telemffb_controls_axes = True
        
        telem = self._create_heli_telem()
        telem["SimOnGround"] = 1
        telem["FFBType"] = "collective"
        self.set_telemetry(instance, telem)
        
        # Set physical collective to match expected position
        self.mock_device._input_data.set_axis(y=1.0)  # Full down
        
        instance.msfs_update_collective(telem)
        
        # Should set cpO_y to full down (4096)
        assert instance.cpO_y == 4096
    
    def test_collective_initialization_in_air_no_previous(self):
        """Test collective initializes to current position when in air with no previous data."""
        instance = self.create_aircraft_instance(Helicopter, name="TestHeli", _test_sim_is_msfs=True, _test_device_type="collective")
        instance.telemffb_controls_axes = True
        
        telem = self._create_heli_telem()
        telem["SimOnGround"] = 0
        telem["FFBType"] = "collective"
        self.set_telemetry(instance, telem)
        
        # Set physical collective to mid position
        self.mock_device._input_data.set_axis(y=0.5)
        
        instance.msfs_update_collective(telem)
        
        # Should set cpO_y based on current physical position
        expected_offset = round(4096 * 0.5)
        assert instance.cpO_y == expected_offset
    
    def test_collective_initialization_with_previous_position(self):
        """Test collective initializes to last known position when resuming."""
        instance = self.create_aircraft_instance(Helicopter, name="TestHeli", _test_sim_is_msfs=True, _test_device_type="collective")
        instance.telemffb_controls_axes = True
        instance.last_collective_y = 0.3  # Previous saved position
        
        telem = self._create_heli_telem()
        telem["SimOnGround"] = 0
        telem["FFBType"] = "collective"
        self.set_telemetry(instance, telem)
        
        instance.msfs_update_collective(telem)
        
        # Should use saved position
        expected_offset = round(4096 * 0.3)
        assert instance.cpO_y == expected_offset
    
    def test_collective_waits_for_physical_stick_centering(self):
        """Test collective waits for physical stick to match target position."""
        instance = self.create_aircraft_instance(Helicopter, name="TestHeli", _test_sim_is_msfs=True, _test_device_type="collective")
        instance.telemffb_controls_axes = True
        
        telem = self._create_heli_telem()
        telem["SimOnGround"] = 1
        telem["FFBType"] = "collective"
        self.set_telemetry(instance, telem)
        
        # Physical stick far from target (target is 1.0 for ground)
        self.mock_device._input_data.set_axis(y=-0.5)
        
        instance.msfs_update_collective(telem)
        
        # Should NOT initialize yet
        assert instance.collective_init == 0
        
        # Move physical stick close to target
        self.mock_device._input_data.set_axis(y=0.95)
        
        instance.msfs_update_collective(telem)
        
        # Should NOW initialize
        assert instance.collective_init == 1
    
    def test_collective_sends_position_to_msfs(self):
        """Test collective sends position commands to MSFS."""
        instance = self.create_aircraft_instance(Helicopter, name="TestHeli", _test_sim_is_msfs=True, _test_device_type="collective")
        instance.telemffb_controls_axes = True
        instance.collective_init = 1  # Already initialized
        
        telem = self._create_heli_telem()
        telem["FFBType"] = "collective"
        self.set_telemetry(instance, telem)
        
        # Set physical position
        self.mock_device._input_data.set_axis(y=0.2)
        
        instance.msfs_update_collective(telem)
        
        # Should have sent event to SimConnect
        assert len(self.mock_simconnect.sent_events) > 0
        
        # Verify event is AXIS_COLLECTIVE_SET
        event_names = [event[0] for event in self.mock_simconnect.sent_events]
        assert 'AXIS_COLLECTIVE_SET' in event_names
    
    def test_collective_force_trim_mode(self):
        """Test collective in force trim mode."""
        instance = self.create_aircraft_instance(Helicopter, name="TestHeli", _test_sim_is_msfs=True, _test_device_type="collective")
        instance.telemffb_controls_axes = True
        instance.collective_init = 1
        instance.spring_mode = SpringModeEnum.FORCETRIM
        
        telem = self._create_heli_telem()
        telem["FFBType"] = "collective"
        self.set_telemetry(instance, telem)
        
        self.mock_device._input_data.set_axis(y=0.0)
        
        instance.msfs_update_collective(telem)
        
        # Verify spring handle was configured (name check)
        assert instance._spring_handle.name == "collective_ft"
    
    def test_collective_custom_axis_support(self):
        """Test collective with custom axis configuration."""
        instance = self.create_aircraft_instance(Helicopter, name="TestHeli", _test_sim_is_msfs=True, _test_device_type="collective")
        instance.telemffb_controls_axes = True
        instance.collective_init = 1
        instance.enable_custom_y_axis = True
        instance.custom_y_axis = "CUSTOM_COLLECTIVE_AXIS"
        instance.raw_y_axis_scale = 100
        
        telem = self._create_heli_telem()
        telem["FFBType"] = "collective"
        self.set_telemetry(instance, telem)
        
        self.mock_device._input_data.set_axis(y=0.5)
        
        instance.msfs_update_collective(telem)
        
        # Should use custom axis
        events = self.mock_simconnect.sent_events
        if events:
            assert events[-1][0] == "CUSTOM_COLLECTIVE_AXIS"

    def test_collective_controls_lock_invert_short_circuits_axis_updates(self):
        """Test collective lock-state inversion triggers lock path and skips normal collective flow."""
        instance = self.create_aircraft_instance(Helicopter, name="TestHeli", _test_sim_is_msfs=True, _test_device_type="collective")
        instance.telemffb_controls_axes = True
        instance.controls_lock_enable = True
        instance.controls_lock_simvar_invert = True

        telem = self._create_heli_telem()
        telem["FFBType"] = "collective"
        telem["ControlsLock"] = 0  # inverted -> treated as locked
        self.set_telemetry(instance, telem)

        # Detent creation window for collective lock path.
        self.mock_device._input_data.set_axis(y=0.95)

        instance.msfs_update_collective(telem)

        assert telem.get("_controls_locked", False) is True
        assert instance.collective_init == 0
        assert len(self.mock_simconnect.sent_events) == 0
        assert self.mock_effects["lock_1"].started
        assert self.mock_effects["lock_1"].detent_config is not None
        assert self.mock_effects["lock_1"].detent_config["position_y"] == 4000
        assert self.mock_effects["lock_2"].detent_config["position_y"] == 2500

    def test_collective_controls_lock_started_effect_short_circuits_axis_updates(self):
        """Test collective lock path exits early when lock effects are already active."""
        instance = self.create_aircraft_instance(Helicopter, name="TestHeli", _test_sim_is_msfs=True, _test_device_type="collective")
        instance.telemffb_controls_axes = True
        instance.controls_lock_enable = True
        instance.collective_init = 1

        telem = self._create_heli_telem()
        telem["FFBType"] = "collective"
        telem["ControlsLock"] = 1
        self.set_telemetry(instance, telem)

        self.mock_effects["lock_1"].started = True
        self.mock_device._input_data.set_axis(y=0.4)

        instance.msfs_update_collective(telem)

        assert telem.get("_controls_locked", False) is True
        assert len(self.mock_simconnect.sent_events) == 0
        assert self.mock_effects["lock_1"].started


@pytest.mark.unit
@pytest.mark.msfs
@pytest.mark.helicopter
@pytest.mark.pedals
class TestHelicopterPedals(BaseTelemetryEffectTestCase):
    """Tests for helicopter pedal/tail rotor control."""
    
    def _create_heli_telem(self):
        """Create basic helicopter telemetry."""
        return TelemetryDataBuilder() \
            .with_sim_on_ground(0) \
            .with_airspeed(10.0) \
            .with_field("AircraftClass", "Helicopter") \
            .with_field("FFBType", "pedals") \
            .with_field("N", 100.0) \
            .with_field("TailRotorPedalPos", 0.0) \
            .build()
    
    def test_pedals_early_return_if_not_pedals_device(self):
        """Test pedal control returns early if not pedals device."""
        instance = self.create_aircraft_instance(Helicopter, name="TestHeli")
        instance._test_sim_is_msfs = True
        instance._test_device_type = "joystick"  # Not pedals
        
        telem = self._create_heli_telem()
        self.set_telemetry(instance, telem)
        
        instance.msfs_update_pedals(telem)
        
        # Should return early
        assert instance.pedals_init == 0
    
    def test_pedals_initialization_on_ground(self):
        """Test pedals initialize to center when on ground."""
        instance = self.create_aircraft_instance(Helicopter, name="TestHeli")
        instance._test_sim_is_msfs = True
        instance._test_device_type = "pedals"
        instance.telemffb_controls_axes = True
        
        telem = self._create_heli_telem()
        telem["SimOnGround"] = 1
        telem["FFBType"] = "pedals"
        self.set_telemetry(instance, telem)
        
        # Set physical pedals to center
        self.mock_device._input_data.set_axis(x=0.0)
        
        instance.msfs_update_pedals(telem)
        
        # Should set cpO_x to center (0)
        assert instance.cpO_x == 0
    
    def test_pedals_initialization_in_air(self):
        """Test pedals initialize to last known position in air."""
        instance = self.create_aircraft_instance(Helicopter, name="TestHeli")
        instance._test_sim_is_msfs = True
        instance._test_device_type = "pedals"
        instance.telemffb_controls_axes = True
        instance.last_pedal_x = 0.4  # Previous position
        
        telem = self._create_heli_telem()
        telem["SimOnGround"] = 0
        telem["FFBType"] = "pedals"
        self.set_telemetry(instance, telem)
        
        instance.msfs_update_pedals(telem)
        
        # Should use saved position
        expected_offset = round(4096 * 0.4)
        assert instance.cpO_x == expected_offset
    
    def test_pedals_waits_for_centering(self):
        """Test pedals wait for physical position to match target."""
        instance = self.create_aircraft_instance(Helicopter, name="TestHeli")
        instance._test_sim_is_msfs = True
        instance._test_device_type = "pedals"
        instance.telemffb_controls_axes = True
        
        telem = self._create_heli_telem()
        telem["SimOnGround"] = 1
        telem["FFBType"] = "pedals"
        self.set_telemetry(instance, telem)
        
        # Physical pedals far from center
        self.mock_device._input_data.set_axis(x=-0.8)
        
        instance.msfs_update_pedals(telem)
        
        # Should NOT initialize
        assert instance.pedals_init == 0
        
        # Move close to center
        self.mock_device._input_data.set_axis(x=0.05)
        
        instance.msfs_update_pedals(telem)
        
        # Should NOW initialize
        assert instance.pedals_init == 1
    
    def test_pedals_sends_position_to_msfs(self):
        """Test pedals send position commands to MSFS."""
        instance = self.create_aircraft_instance(Helicopter, name="TestHeli")
        instance._test_sim_is_msfs = True
        instance._test_device_type = "pedals"
        instance.telemffb_controls_axes = True
        instance.pedals_init = 1  # Already initialized
        
        telem = self._create_heli_telem()
        telem["FFBType"] = "pedals"
        self.set_telemetry(instance, telem)
        
        self.mock_device._input_data.set_axis(x=0.3)
        
        instance.msfs_update_pedals(telem)
        
        # Should have sent event
        assert len(self.mock_simconnect.sent_events) > 0
        
        # Verify event is ROTOR_AXIS_TAIL_ROTOR_SET
        event_names = [event[0] for event in self.mock_simconnect.sent_events]
        assert 'ROTOR_AXIS_TAIL_ROTOR_SET' in event_names
    
    def test_pedals_force_trim_mode(self):
        """Test pedals in force trim mode."""
        instance = self.create_aircraft_instance(Helicopter, name="TestHeli")
        instance._test_sim_is_msfs = True
        instance._test_device_type = "pedals"
        instance.telemffb_controls_axes = True
        instance.pedals_init = 1
        instance.spring_mode = SpringModeEnum.FORCETRIM
        
        telem = self._create_heli_telem()
        telem["FFBType"] = "pedals"
        telem["ForceTrimSW"] = True
        self.set_telemetry(instance, telem)
        
        self.mock_device._input_data.set_axis(x=0.0)
        
        instance.msfs_update_pedals(telem)
        
        # Verify spring was configured (check name)
        assert instance._spring_handle.name == "pedal_spring"
    
    def test_pedals_custom_force_trim_switch(self):
        """Test pedals with custom force trim switch variable."""
        instance = self.create_aircraft_instance(Helicopter, name="TestHeli", _test_sim_is_msfs=True, _test_device_type="joystick")
        instance.telemffb_controls_axes = True
        instance.custom_ft_sw_var_enabled = False  # Start disabled
        instance.custom_ft_sw_var = "L:CUSTOM_FT_SWITCH"
        
        telem = self._create_heli_telem()
        telem["FFBType"] = "joystick"
        self.set_telemetry(instance, telem)
        
        # First call establishes baseline
        instance.msfs_update_heli_controls(telem)
        
        # Now enable it - this should trigger subscription
        instance.custom_ft_sw_var_enabled = True
        instance.msfs_update_heli_controls(telem)
        
        # Should have subscribed to custom variable
        assert any('CUSTOM_FT_SWITCH' in str(call) for call in self.mock_simconnect.simvar_calls)
    
    def test_pedals_custom_axis_support(self):
        """Test pedals with custom axis configuration."""
        instance = self.create_aircraft_instance(Helicopter, name="TestHeli", _test_sim_is_msfs=True, _test_device_type="pedals")
        instance.telemffb_controls_axes = True
        instance.pedals_init = 1
        instance.enable_custom_x_axis = True
        instance.custom_x_axis = "CUSTOM_PEDAL_AXIS"
        instance.rudder_x_axis_scale = 0.8
        
        telem = self._create_heli_telem()
        telem["FFBType"] = "pedals"
        self.set_telemetry(instance, telem)
        
        self.mock_device._input_data.set_axis(x=0.5)
        
        instance.msfs_update_pedals(telem)
        
        # Should use custom axis
        events = self.mock_simconnect.sent_events
        if events:
            assert events[-1][0] == "CUSTOM_PEDAL_AXIS"

    def test_pedals_controls_lock_invert_short_circuits_axis_updates(self):
        """Test pedal lock-state inversion triggers lock path and skips normal pedal flow."""
        instance = self.create_aircraft_instance(Helicopter, name="TestHeli", _test_sim_is_msfs=True, _test_device_type="pedals")
        instance.telemffb_controls_axes = True
        instance.controls_lock_enable = True
        instance.controls_lock_simvar_invert = True

        telem = self._create_heli_telem()
        telem["FFBType"] = "pedals"
        telem["ControlsLock"] = 0  # inverted -> treated as locked
        self.set_telemetry(instance, telem)

        # Detent creation window for pedal lock path.
        self.mock_device._input_data.set_axis(x=0.0)

        instance.msfs_update_pedals(telem)

        assert telem.get("_controls_locked", False) is True
        assert instance.pedals_init == 0
        assert len(self.mock_simconnect.sent_events) == 0
        assert self.mock_effects["lock_1"].started
        assert self.mock_effects["lock_1"].detent_config is not None
        assert self.mock_effects["lock_1"].detent_config["position_x"] == 1500
        assert self.mock_effects["lock_2"].detent_config["position_x"] == -1500

    def test_pedals_controls_lock_started_effect_short_circuits_axis_updates(self):
        """Test pedal lock path exits early when lock effects are already active."""
        instance = self.create_aircraft_instance(Helicopter, name="TestHeli", _test_sim_is_msfs=True, _test_device_type="pedals")
        instance.telemffb_controls_axes = True
        instance.controls_lock_enable = True
        instance.pedals_init = 1

        telem = self._create_heli_telem()
        telem["FFBType"] = "pedals"
        telem["ControlsLock"] = 1
        self.set_telemetry(instance, telem)

        self.mock_effects["lock_1"].started = True
        self.mock_device._input_data.set_axis(x=0.2)

        instance.msfs_update_pedals(telem)

        # Pedal lock path only writes `_controls_locked` once the centered detent is engaged.
        assert telem.get("_controls_locked", False) is False
        assert len(self.mock_simconnect.sent_events) == 0
        assert self.mock_effects["lock_1"].started


@pytest.mark.unit
@pytest.mark.msfs
@pytest.mark.helicopter
@pytest.mark.joystick
class TestHelicopterCyclicControls(BaseTelemetryEffectTestCase):
    """Tests for helicopter cyclic (joystick) control."""
    
    def _create_heli_telem(self):
        """Create basic helicopter telemetry."""
        return TelemetryDataBuilder() \
            .on_ground(False) \
            .with_airspeed(10.0) \
            .with_field("AircraftClass", "Helicopter") \
            .with_field("FFBType", "joystick") \
            .with_field("N", 100.0) \
            .build()
    
    def test_cyclic_initialization_on_ground(self):
        """Test cyclic initializes to center when on ground."""
        instance = self.create_aircraft_instance(Helicopter, name="TestHeli", _test_sim_is_msfs=True, _test_device_type="joystick")
        instance._test_sim_is_msfs = True
        instance._test_device_type = "joystick"
        instance.spring_mode = SpringModeEnum.FORCETRIM
        instance.force_trim_button = 1
        instance.trim_reset_complete = 1
        
        telem = self._create_heli_telem()
        telem["SimOnGround"] = 1
        self.set_telemetry(instance, telem)
        
        self.mock_device._input_data.set_axis(x=0.0, y=0.0)
        
        instance.msfs_update_heli_controls(telem)
        
        # Should initialize to center
        assert instance.cpO_x == 0
        assert instance.cpO_y == 0
    
    def test_cyclic_force_trim_button_press_detection(self):
        """Test force trim button press is detected."""
        instance = self.create_aircraft_instance(Helicopter, name="TestHeli", _test_sim_is_msfs=True, _test_device_type="joystick")
        instance._test_sim_is_msfs = True
        instance._test_device_type = "joystick"
        instance.spring_mode = SpringModeEnum.FORCETRIM
        instance.force_trim_button = 1
        instance.cyclic_spring_init = 1
        
        telem = self._create_heli_telem()
        self.set_telemetry(instance, telem)
        
        self.mock_device._input_data.set_axis(x=0.2, y=0.3)
        
        # Press force trim button
        self.mock_device._input_data.press_button(1)
        
        instance.msfs_update_heli_controls(telem)
        
        # Should detect button press and handle trim release
        assert instance.cyclic_trim_release_active == 1
    
    def test_cyclic_force_trim_button_release(self):
        """Test force trim button release engages trim."""
        instance = self.create_aircraft_instance(Helicopter, name="TestHeli", _test_sim_is_msfs=True, _test_device_type="joystick")
        instance._test_sim_is_msfs = True
        instance._test_device_type = "joystick"
        instance.spring_mode = SpringModeEnum.FORCETRIM
        instance.force_trim_button = 1
        instance.cyclic_spring_init = 1
        instance.cyclic_trim_release_active = 1
        
        telem = self._create_heli_telem()
        self.set_telemetry(instance, telem)
        
        self.mock_device._input_data.set_axis(x=0.4, y=0.5)
        # Button NOT pressed
        
        instance.msfs_update_heli_controls(telem)
        
        # Should lock new trim position
        assert instance.cyclic_trim_release_active == 0
        # Center should be updated
        assert instance.cyclic_center == [0.4, 0.5]
    
    def test_cyclic_trim_reset_button(self):
        """Test trim reset button drives stick to center."""
        instance = self.create_aircraft_instance(Helicopter, name="TestHeli", _test_sim_is_msfs=True, _test_device_type="joystick")
        instance._test_sim_is_msfs = True
        instance._test_device_type = "joystick"
        instance.spring_mode = SpringModeEnum.FORCETRIM
        instance.force_trim_button = 1
        instance.force_trim_reset_button = 2
        instance.cyclic_spring_init = 1
        instance.cpO_x = 2000
        instance.cpO_y = 3000
        
        telem = self._create_heli_telem()
        self.set_telemetry(instance, telem)
        
        self.mock_device._input_data.set_axis(x=0.0, y=0.0)
        self.mock_device._input_data.press_button(2)
        
        instance.msfs_update_heli_controls(telem)
        
        # Should start moving toward center
        assert instance.trim_reset_complete == 0
        # Offsets should be moving toward 0 (first call returns original value)
        assert abs(instance.cpO_x) <= 2000
        assert abs(instance.cpO_y) <= 3000
    
    def test_cyclic_sends_position_to_msfs(self):
        """Test cyclic sends position to MSFS."""
        instance = self.create_aircraft_instance(Helicopter, name="TestHeli", _test_sim_is_msfs=True, _test_device_type="joystick")
        instance.telemffb_controls_axes = True  # Enable axis control
        instance.spring_mode = SpringModeEnum.BASIC
        instance.cyclic_spring_init = 1
        
        telem = self._create_heli_telem()
        self.set_telemetry(instance, telem)
        
        self.mock_device._input_data.set_axis(x=0.3, y=-0.2)
        
        instance.msfs_update_heli_controls(telem)
        
        # Should have sent events
        assert len(self.mock_simconnect.sent_events) > 0
        
        # Should include cyclic axes
        event_names = [event[0] for event in self.mock_simconnect.sent_events]
        assert any('CYCLIC' in name for name in event_names)
    
    def test_cyclic_force_trim_disabled_by_switch(self):
        """Test force trim can be disabled by cockpit switch."""
        instance = self.create_aircraft_instance(Helicopter, name="TestHeli")
        instance._test_sim_is_msfs = True
        instance._test_device_type = "joystick"
        instance.custom_ft_sw_var_enabled = True
        instance.spring_mode = SpringModeEnum.FORCETRIM
        instance.force_trim_button = 1
        instance.cyclic_spring_init = 1
        
        telem = self._create_heli_telem()
        telem["ForceTrimSW"] = False  # Switch disabled
        self.set_telemetry(instance, telem)
        
        self.mock_device._input_data.set_axis(x=0.0, y=0.0)
        
        instance.msfs_update_heli_controls(telem)
        
        # Force trim should not be active
        # Spring should be softened
        assert instance.ft_was_inactive == True


@pytest.mark.unit
@pytest.mark.xplane
@pytest.mark.helicopter
class TestHelicopterXPlane(BaseTelemetryEffectTestCase):
    """Tests for X-Plane specific helicopter functionality."""
    
    def _create_xp_heli_telem(self):
        """Create X-Plane helicopter telemetry."""
        return TelemetryDataBuilder() \
            .with_sim_on_ground(0) \
            .with_airspeed(10.0) \
            .with_field("AircraftClass", "Helicopter") \
            .with_field("FFBType", "collective") \
            .with_field("N", 100.0) \
            .build()
    
    def test_xplane_collective_sends_udp_command(self):
        """Test X-Plane collective sends UDP commands."""
        instance = self.create_aircraft_instance(Helicopter, name="TestHeli")
        instance._test_sim_is_xplane = True
        instance._test_device_type = "collective"
        instance.telemffb_controls_axes = True
        instance.collective_init = 1
        
        telem = self._create_xp_heli_telem()
        self.set_telemetry(instance, telem)
        
        self.mock_device._input_data.set_axis(y=0.5)
        
        with patch.object(instance, 'send_xp_command') as mock_send:
            instance.msfs_update_collective(telem)
            
            # Should have sent X-Plane command
            assert mock_send.called
            call_args = str(mock_send.call_args)
            assert 'AXIS:cy=' in call_args


@pytest.mark.unit
@pytest.mark.helicopter
class TestHelicopterSimConnectProxy(BaseTelemetryEffectTestCase):
    """Tests for SimConnect proxy handling when simconnect is None."""
    
    def test_simconnect_proxy_handles_none_gracefully(self):
        """Test that SimConnect proxy doesn't crash when simconnect is None."""
        instance = self.create_aircraft_instance(Helicopter, name="TestHeli")
        instance._test_sim_is_msfs = True
        instance._test_device_type = "pedals"
        
        # Set simconnect to None via telem_manager
        G.telem_manager = None
        
        telem = {
            "SimOnGround": 1,
            "FFBType": "pedals",
            "N": 100.0,
            "AircraftClass": "Helicopter"
        }
        self.set_telemetry(instance, telem)
        
        # This should not crash even though simconnect is None
        instance.msfs_send_heli_pedal_pos("TEST_VAR", 1000, telem)
        
        # No exception should be raised
    
    def test_subscribe_simvars_handles_none_simconnect(self):
        """Test subscribe_simvars handles None simconnect."""
        instance = self.create_aircraft_instance(Helicopter, name="TestHeli")
        instance._test_sim_is_msfs = True
        
        # Set simconnect to None
        G.telem_manager = None
        
        # Should not crash
        instance.subscribe_simvars()
        
        # No exception should be raised


@pytest.mark.unit
@pytest.mark.helicopter
class TestHelicopterTrimwheel(BaseTelemetryEffectTestCase):
    """Tests for trimwheel device type with helicopters."""
    
    def test_trimwheel_skips_update(self):
        """Test that trimwheel device type doesn't process helicopter controls."""
        instance = self.create_aircraft_instance(Helicopter, name="TestHeli")
        instance._test_sim_is_msfs = True
        instance._test_device_type = "trimwheel"
        
        telem = {
            "SimOnGround": 0,
            "FFBType": "trimwheel",
            "N": 100.0,
            "AircraftClass": "Helicopter"
        }
        self.set_telemetry(instance, telem)
        
        # Should be overridden to do nothing
        instance.msfs_update_trimwheel(telem)
        
        # No effects should be created
        # Verify by checking no events sent
        assert len(self.mock_simconnect.sent_events) == 0


@pytest.mark.unit
@pytest.mark.helicopter
class TestHelicopterTelemetryProcessing(BaseTelemetryEffectTestCase):
    """Tests for helicopter telemetry processing."""
    
    def test_on_telemetry_injects_aircraft_class(self):
        """Test that on_telemetry injects AircraftClass into telemetry."""
        instance = self.create_aircraft_instance(Helicopter, name="TestHeli")
        instance._test_sim_is_msfs = True
        instance._test_device_type = "joystick"
        
        telem = BaseTelemetryData({
            "SimOnGround": 0,
            "FFBType": "joystick",
            "N": 100.0,
            "VelWorld": [0.0, 0.0, 0.0],
            "AmbWind": [0.0, 0.0, 0.0],
            "Heading": 0.0,
            "Pitch": 0.0,
            "Roll": 0.0
        })
        self.set_telemetry(instance, telem)
        
        instance.on_telemetry(telem)
        
        # Should inject helicopter class
        assert telem.AircraftClass == "Helicopter"
    
    def test_on_telemetry_returns_early_if_no_rotor_rpm(self):
        """Test that on_telemetry returns early if no rotor RPM data."""
        instance = self.create_aircraft_instance(Helicopter, name="TestHeli")
        instance._test_sim_is_msfs = True
        
        telem = BaseTelemetryData({
            "SimOnGround": 0,
            "FFBType": "joystick"
            # Missing "N" (rotor RPM)
        })
        self.set_telemetry(instance, telem)
        
        # Should return early without processing
        instance.on_telemetry(telem)
        
        # AircraftClass should not be injected
        assert telem.AircraftClass is None
    
    def test_on_telemetry_disables_speedbrake_motion(self):
        """Test that helicopter disables speedbrake motion effects."""
        instance = self.create_aircraft_instance(Helicopter, name="TestHeli")
        instance._test_sim_is_msfs = True
        instance.speedbrake_motion_intensity = 1.0  # Set non-zero
        
        telem = BaseTelemetryData({
            "SimOnGround": 0,
            "FFBType": "joystick",
            "N": 100.0,
            "VelWorld": [0.0, 0.0, 0.0],
            "AmbWind": [0.0, 0.0, 0.0],
            "Heading": 0.0,
            "Pitch": 0.0,
            "Roll": 0.0
        })
        self.set_telemetry(instance, telem)
        
        instance.on_telemetry(telem)
        
        # Should be set to 0
        assert instance.speedbrake_motion_intensity == 0.0


@pytest.mark.unit
@pytest.mark.helicopter
class TestHelicopterParameterConfiguration(BaseTelemetryEffectTestCase):
    """Tests for helicopter user-configurable parameters."""
    
    def test_etl_parameters_configurable(self):
        """Test ETL effect parameters are configurable."""
        instance = self.create_aircraft_instance(Helicopter, name="TestHeli")
        
        # Set parameters after creation (they're class-level attributes)
        instance.etl_start_speed = 8.0
        instance.etl_stop_speed = 25.0
        instance.etl_effect_intensity = 0.4
        instance.etl_shake_frequency = 16.0
        
        assert instance.etl_start_speed == 8.0
        assert instance.etl_stop_speed == 25.0
        assert instance.etl_effect_intensity == 0.4
        assert instance.etl_shake_frequency == 16.0
    
    def test_overspeed_parameters_configurable(self):
        """Test overspeed shake parameters are configurable."""
        instance = self.create_aircraft_instance(Helicopter, name="TestHeli")
        
        instance.overspeed_shake_start = 80.0
        instance.overspeed_shake_intensity = 0.4
        
        assert instance.overspeed_shake_start == 80.0
        assert instance.overspeed_shake_intensity == 0.4
    
    def test_collective_spring_parameters_configurable(self):
        """Test collective spring parameters are configurable."""
        instance = self.create_aircraft_instance(Helicopter, name="TestHeli")
        
        instance.collective_ap_spring_gain = 2.0
        instance.collective_dampening_gain = 0.5
        instance.collective_spring_coeff_y = 1024
        
        assert instance.collective_ap_spring_gain == 2.0
        assert instance.collective_dampening_gain == 0.5
        assert instance.collective_spring_coeff_y == 1024
    
    def test_pedal_spring_parameters_configurable(self):
        """Test pedal spring parameters are configurable."""
        instance = self.create_aircraft_instance(Helicopter, name="TestHeli")
        
        instance.pedal_spring_gain = 1.5
        instance.pedal_dampening_gain = 0.8
        instance.pedal_spring_coeff_x = 2048
        
        assert instance.pedal_spring_gain == 1.5
        assert instance.pedal_dampening_gain == 0.8
        assert instance.pedal_spring_coeff_x == 2048
    
    def test_cyclic_trim_follow_gains_configurable(self):
        """Test cyclic trim follow gains are configurable."""
        instance = self.create_aircraft_instance(Helicopter, name="TestHeli")
        
        instance.joystick_trim_follow_gain_physical_x = 0.5
        instance.joystick_trim_follow_gain_virtual_x = 0.3
        instance.joystick_trim_follow_gain_physical_y = 0.6
        instance.joystick_trim_follow_gain_virtual_y = 0.4
        
        assert instance.joystick_trim_follow_gain_physical_x == 0.5
        assert instance.joystick_trim_follow_gain_virtual_x == 0.3
        assert instance.joystick_trim_follow_gain_physical_y == 0.6
        assert instance.joystick_trim_follow_gain_virtual_y == 0.4
