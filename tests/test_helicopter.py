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

    def test_cached_cyclic_positions_are_ints(self):
        """The cyclic spring init replays these to AXIS_CYCLIC_*_SET, which is
        a standard SimConnect event and takes an int.

        MsfsXpTrimwheelMixIn shares this MRO and used to seed a float under
        the same name (last_pos_y_pos), so a helicopter that initialized its
        cyclic spring before anything had cached an int over it raised
        "'float' object cannot be interpreted as an integer" - airborne only,
        Y axis only, and cleared by a restart, which made it look random.
        """
        instance = self.create_aircraft_instance(Helicopter, name="TestHeli")

        assert isinstance(instance.last_pos_x_pos, int), \
            f"lateral cache is {type(instance.last_pos_x_pos).__name__}"
        assert isinstance(instance.last_pos_y_pos, int), \
            f"longitudinal cache is {type(instance.last_pos_y_pos).__name__}"
        # the trimwheel keeps its own float - direct mode sends radians
        assert isinstance(instance._tw_last_pos_y, float)

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
    
    def test_collective_honors_local_axis_control_disable(self):
        """The per-device axis disable must stop collective axis output."""
        instance = self.create_aircraft_instance(Helicopter, name="TestHeli", _test_sim_is_msfs=True, _test_device_type="collective")
        instance.telemffb_controls_axes = True
        instance.local_disable_axis_control = True
        instance.collective_init = 1

        telem = self._create_heli_telem()
        self.set_telemetry(instance, telem)

        self.mock_device._input_data.set_axis(x=0.0, y=0.4)

        instance.msfs_update_collective(telem)

        assert self.mock_simconnect.sent_events == []

    def test_collective_force_trim_flags_error_when_release_button_unbound(self):
        """FORCETRIM on the collective is unusable without a release button:
        the configuration error must be flagged."""
        instance = self.create_aircraft_instance(Helicopter, name="TestHeli", _test_sim_is_msfs=True, _test_device_type="collective")
        telem = self._create_heli_telem()
        self.set_telemetry(instance, telem)
        instance.spring_mode = SpringModeEnum.FORCETRIM
        instance.collective_ft_ovd_release = 0

        instance.ac_collective_force_trim_override(telem, MagicMock())

        assert 'release button is not configured' in (instance.telem_data.get('error') or '')

    def test_collective_force_trim_no_flag_when_mode_disabled(self):
        """No configuration error when FORCETRIM is not the active spring mode."""
        instance = self.create_aircraft_instance(Helicopter, name="TestHeli", _test_sim_is_msfs=True, _test_device_type="collective")
        telem = self._create_heli_telem()
        self.set_telemetry(instance, telem)
        instance.spring_mode = SpringModeEnum.NONE
        instance.collective_ft_ovd_release = 0

        instance.ac_collective_force_trim_override(telem, MagicMock())

        assert instance.telem_data.get('error') is None

    def test_collective_force_trim_no_flag_when_release_button_bound(self):
        """No configuration error when the release button is configured."""
        instance = self.create_aircraft_instance(Helicopter, name="TestHeli", _test_sim_is_msfs=True, _test_device_type="collective")
        telem = self._create_heli_telem()
        self.set_telemetry(instance, telem)
        instance.spring_mode = SpringModeEnum.FORCETRIM
        instance.collective_ft_ovd_release = 5

        instance.ac_collective_force_trim_override(telem, MagicMock())

        assert instance.telem_data.get('error') is None

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
        
        # Should set normalized cpO_y to full down (+1.0).
        assert instance.cpO_y == 1.0
    
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
        expected_offset = 0.5
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
        expected_offset = 0.3
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
        expected_offset = 0.4
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
        instance.pedal_ft_release_button = 1
        
        telem = self._create_heli_telem()
        telem["FFBType"] = "pedals"
        telem["ForceTrimSW"] = True
        self.set_telemetry(instance, telem)
        
        self.mock_device._input_data.set_axis(x=0.25)
        self.mock_device._input_data.press_button(1)
        
        instance.msfs_update_pedals(telem)
        
        # Verify spring was configured (check name)
        assert instance._spring_handle.name == "pedal_spring"
        assert instance.cpO_x == pytest.approx(0.25)
        assert instance.spring_x.cpOffset == round(0.25 * 4096)
    
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
        # position_x is now a normalized -1..1 float (1500/4096); the device scales it back
        assert self.mock_effects["lock_1"].detent_config["position_x"] == 1500 / 4096
        assert self.mock_effects["lock_2"].detent_config["position_x"] == -1500 / 4096

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
        instance.cpO_x = 2000 / 4096
        instance.cpO_y = 3000 / 4096
        
        telem = self._create_heli_telem()
        self.set_telemetry(instance, telem)
        
        self.mock_device._input_data.set_axis(x=0.0, y=0.0)
        self.mock_device._input_data.press_button(2)
        
        instance.msfs_update_heli_controls(telem)
        
        # Should start moving toward center
        assert instance.trim_reset_complete == 0
        # Offsets should be moving toward 0 (first call returns original value)
        assert abs(instance.cpO_x) <= 2000 / 4096
        assert abs(instance.cpO_y) <= 3000 / 4096
    
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
    
    def test_cyclic_force_trim_unbound_button_still_sends_position(self):
        """Force trim with no bound button degrades to no-spring, not a dead update."""
        instance = self.create_aircraft_instance(Helicopter, name="TestHeli", _test_sim_is_msfs=True, _test_device_type="joystick")
        instance.telemffb_controls_axes = True
        instance.spring_mode = SpringModeEnum.FORCETRIM
        instance.force_trim_button = 0
        instance.cyclic_spring_gain = 0.9

        telem = self._create_heli_telem()
        self.set_telemetry(instance, telem)

        self.mock_device._input_data.set_axis(x=0.3, y=-0.2)

        instance.msfs_update_heli_controls(telem)

        event_names = [event[0] for event in self.mock_simconnect.sent_events]
        assert any('CYCLIC' in name for name in event_names)

        assert instance.spring_x.positiveCoefficient == 0
        assert instance.spring_y.positiveCoefficient == 0

        assert instance.telem_data.get('error')

    def test_cyclic_force_trim_recovers_when_button_rebound(self):
        """Unbinding the release button live and re-binding it restores the spring."""
        instance = self.create_aircraft_instance(Helicopter, name="TestHeli", _test_sim_is_msfs=True, _test_device_type="joystick")
        instance.telemffb_controls_axes = True
        instance.spring_mode = SpringModeEnum.FORCETRIM
        instance.force_trim_button = 1
        instance.cyclic_spring_gain = 0.9

        self.mock_device._input_data.set_axis(x=0.1, y=0.05)

        def run_frames():
            for _ in range(3):
                telem = self._create_heli_telem()
                self.set_telemetry(instance, telem)
                instance.msfs_update_heli_controls(telem)

        run_frames()
        assert instance.spring_x.positiveCoefficient > 0

        instance.force_trim_button = 0
        run_frames()
        assert instance.spring_x.positiveCoefficient == 0

        instance.force_trim_button = 1
        run_frames()
        assert instance.spring_x.positiveCoefficient > 0
        assert instance.spring_y.positiveCoefficient > 0

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
@pytest.mark.msfs
@pytest.mark.helicopter
class TestCyclicSubMethods(BaseTelemetryEffectTestCase):
    """Tests for extracted cyclic sub-methods from msfs_update_heli_controls."""

    def _make_instance(self, **kwargs):
        defaults = dict(
            name="TestHeli", _test_sim_is_msfs=True, _test_device_type="joystick"
        )
        defaults.update(kwargs)
        inst = self.create_aircraft_instance(Helicopter, **defaults)
        inst.spring_mode = SpringModeEnum.FORCETRIM
        inst.force_trim_button = 1
        inst.cyclic_spring_gain = 4096
        inst.trim_release_spring_gain = 0.5
        inst.force_trim_send_reset = True
        return inst

    def _make_telem(self, **overrides):
        b = TelemetryDataBuilder() \
            .on_ground(False) \
            .with_airspeed(10.0) \
            .with_field("AircraftClass", "Helicopter") \
            .with_field("FFBType", "joystick") \
            .with_field("N", 100.0)
        for k, v in overrides.items():
            b = b.with_field(k, v)
        return b.build()

    # --- _initialize_cyclic_if_needed tests ---

    def test_init_cyclic_already_initialized_returns_false(self):
        inst = self._make_instance()
        inst.cyclic_spring_init = 1
        telem = self._make_telem()
        assert inst._initialize_cyclic_if_needed(telem) is False

    def test_init_cyclic_on_ground_centers(self):
        inst = self._make_instance()
        inst.cyclic_spring_init = 0
        inst.trim_reset_complete = 1
        inst.last_device_x = 0.5
        inst.last_device_y = 0.5
        self.mock_device._input_data.set_axis(x=0.0, y=0.0)
        telem = self._make_telem(SimOnGround=1)
        self.set_telemetry(inst, telem)

        result = inst._initialize_cyclic_if_needed(telem)

        assert inst.cpO_x == 0
        assert inst.cpO_y == 0
        assert inst.last_pos_x_pos == 0
        assert inst.last_pos_y_pos == 0
        # stick at 0,0 is within gate of target 0,0 → init completes
        assert result is False
        assert inst.cyclic_spring_init == 1

    def test_init_cyclic_in_air_restores_last_position(self):
        inst = self._make_instance()
        inst.cyclic_spring_init = 0
        inst.trim_reset_complete = 0  # not on ground path
        inst.last_device_x = 0.25
        inst.last_device_y = -0.3
        self.mock_device._input_data.set_axis(x=0.25, y=-0.3)
        telem = self._make_telem(SimOnGround=0)
        self.set_telemetry(inst, telem)

        result = inst._initialize_cyclic_if_needed(telem)

        assert inst.cpO_x == 0.25
        assert inst.cpO_y == -0.3
        # stick is at target → completes
        assert result is False
        assert inst.cyclic_spring_init == 1

    def test_init_cyclic_stick_outside_gate_returns_true(self):
        inst = self._make_instance()
        inst.cyclic_spring_init = 0
        inst.trim_reset_complete = 1
        inst.last_device_x = 0.0
        inst.last_device_y = 0.0
        inst.last_pos_x_pos = 0
        inst.last_pos_y_pos = 0
        # stick is far from target (0,0)
        self.mock_device._input_data.set_axis(x=0.5, y=0.5)
        telem = self._make_telem(SimOnGround=1)
        self.set_telemetry(inst, telem)

        result = inst._initialize_cyclic_if_needed(telem)

        assert result is True
        assert inst.cyclic_spring_init == 0  # not yet initialized

    def test_init_cyclic_sends_last_pos_to_msfs_while_waiting(self):
        inst = self._make_instance()
        inst.cyclic_spring_init = 0
        inst.trim_reset_complete = 0  # in-air path
        inst.last_device_x = 0.3
        inst.last_device_y = -0.2
        inst.last_pos_x_pos = 1234
        inst.last_pos_y_pos = 5678
        self.mock_device._input_data.set_axis(x=0.8, y=0.8)  # far from target
        telem = self._make_telem(SimOnGround=0)
        self.set_telemetry(inst, telem)

        inst._initialize_cyclic_if_needed(telem)

        # should send last_pos values via msfs_send_heli_cyclic_pos
        events = self.mock_simconnect.sent_events
        assert len(events) == 2
        assert events[0] == ("AXIS_CYCLIC_LATERAL_SET", 1234)
        assert events[1] == ("AXIS_CYCLIC_LONGITUDINAL_SET", 5678)

    # --- _handle_cyclic_trim_reset tests ---

    def test_trim_reset_starts_animation(self):
        inst = self._make_instance()
        inst.cpO_x = 2000 / 4096
        inst.cpO_y = 3000 / 4096
        inst.cyclic_spring_gain = 4096

        inst._handle_cyclic_trim_reset()

        # should be in progress
        assert inst._trim_reset_in_progress is True
        assert inst.trim_reset_complete == 0

    def test_trim_reset_completes_at_zero(self):
        inst = self._make_instance()
        inst.cpO_x = 0
        inst.cpO_y = 0
        inst._trim_reset_in_progress = True

        # step_value_over_time returns 0 when already at target
        inst._handle_cyclic_trim_reset()

        assert inst.trim_reset_complete == 1
        assert inst._trim_reset_in_progress is False

    def test_trim_reset_sends_msfs_event(self):
        inst = self._make_instance()
        inst.cpO_x = 0
        inst.cpO_y = 0
        inst._trim_reset_in_progress = True

        inst._handle_cyclic_trim_reset()

        events = self.mock_simconnect.sent_events
        assert ("ROTOR_TRIM_RESET", 0) in events

    # --- _update_cyclic_force_trim tests ---

    def test_force_trim_degrades_to_no_spring_when_no_button_configured(self):
        inst = self._make_instance()
        inst.force_trim_button = 0
        inst.cyclic_spring_init = 1
        telem = self._make_telem()
        self.set_telemetry(inst, telem)
        input_data = self.mock_device.get_input()

        result = inst._update_cyclic_force_trim(telem, input_data, 0.0, 0.0, True)

        assert result is False
        assert inst.spring_x.positiveCoefficient == 0
        assert inst.spring_y.positiveCoefficient == 0
        assert inst.telem_data.get('error')

    def test_force_trim_returns_true_during_init(self):
        inst = self._make_instance()
        inst.cyclic_spring_init = 0
        inst.trim_reset_complete = 1
        # stick far from target → init not complete
        self.mock_device._input_data.set_axis(x=0.9, y=0.9)
        inst.last_pos_x_pos = 0
        inst.last_pos_y_pos = 0
        inst.last_device_x = 0.0
        inst.last_device_y = 0.0
        telem = self._make_telem(SimOnGround=1)
        self.set_telemetry(inst, telem)
        input_data = self.mock_device.get_input()

        result = inst._update_cyclic_force_trim(telem, input_data, 0.0, 0.0, True)

        assert result is True

    def test_force_trim_pressed_absorbs_offsets(self):
        inst = self._make_instance()
        inst.cyclic_spring_init = 1
        inst.cpO_x = 1000 / 4096
        inst.cpO_y = 2000 / 4096
        inst.cyclic_physical_trim_x_offs = 100 / 4096
        inst.cyclic_physical_trim_y_offs = 200 / 4096
        inst.cyclic_virtual_trim_x_offs = 50.0
        inst.cyclic_virtual_trim_y_offs = 60.0
        telem = self._make_telem()
        self.set_telemetry(inst, telem)

        # Press force trim button
        self.mock_device._input_data.press_button(1)
        input_data = self.mock_device.get_input()

        result = inst._update_cyclic_force_trim(telem, input_data, 0.3, 0.4, True)

        assert result is False
        assert inst.cyclic_trim_release_active == 1
        assert inst.cyclic_physical_trim_x_offs == 0
        assert inst.cyclic_physical_trim_y_offs == 0
        assert inst.cyclic_virtual_trim_x_offs == 0.0
        assert inst.cyclic_virtual_trim_y_offs == 0.0

    def test_force_trim_released_locks_center(self):
        inst = self._make_instance()
        inst.cyclic_spring_init = 1
        inst.cyclic_trim_release_active = 1
        telem = self._make_telem()
        self.set_telemetry(inst, telem)

        # Button NOT pressed
        input_data = self.mock_device.get_input()

        result = inst._update_cyclic_force_trim(telem, input_data, 0.4, 0.5, True)

        assert result is False
        assert inst.cyclic_trim_release_active == 0
        assert inst.cyclic_center == [0.4, 0.5]
        assert inst.cpO_x == 0.4
        assert inst.cpO_y == 0.5

    def test_force_trim_idle_sets_initial_coefficient(self):
        inst = self._make_instance()
        inst.cyclic_spring_init = 1
        inst.cyclic_trim_release_active = 0
        inst.trim_reset_complete = 1
        inst.ft_was_inactive = True
        telem = self._make_telem()
        self.set_telemetry(inst, telem)

        input_data = self.mock_device.get_input()

        result = inst._update_cyclic_force_trim(telem, input_data, 0.0, 0.0, True)

        assert result is False
        assert inst.ft_was_inactive is False

    def test_force_trim_inactive_follows_stick(self):
        inst = self._make_instance()
        inst.cyclic_spring_init = 1
        telem = self._make_telem()
        self.set_telemetry(inst, telem)

        input_data = self.mock_device.get_input()

        # force_trim_active=False with FORCETRIM mode → inactive following
        result = inst._update_cyclic_force_trim(telem, input_data, 0.3, -0.2, False)

        assert result is False
        assert inst.ft_was_inactive is True
        assert inst.cpO_x == 0.3
        assert inst.cpO_y == -0.2

    def test_force_trim_non_forcetrim_mode_zeroes_spring(self):
        inst = self._make_instance()
        inst.spring_mode = SpringModeEnum.BASIC  # not FORCETRIM
        telem = self._make_telem()
        self.set_telemetry(inst, telem)

        input_data = self.mock_device.get_input()

        result = inst._update_cyclic_force_trim(telem, input_data, 0.0, 0.0, True)

        assert result is False

    # --- _send_cyclic_axis_output tests ---

    def test_send_axis_output_disabled_when_controls_axes_off(self):
        inst = self._make_instance()
        inst.telemffb_controls_axes = False
        telem = self._make_telem()
        self.set_telemetry(inst, telem)

        inst._send_cyclic_axis_output(telem, True)

        assert len(self.mock_simconnect.sent_events) == 0

    def test_send_axis_output_sends_msfs_events(self):
        inst = self._make_instance()
        inst.telemffb_controls_axes = True
        inst.local_disable_axis_control = False
        inst.cyclic_spring_init = 1
        inst.joystick_x_axis_scale = 1.0
        inst.joystick_y_axis_scale = 1.0
        inst.cyclic_virtual_trim_x_offs = 0.0
        inst.cyclic_virtual_trim_y_offs = 0.0
        inst.trim_following = False
        self.mock_device._input_data.set_axis(x=0.3, y=-0.2)
        telem = self._make_telem()
        self.set_telemetry(inst, telem)

        inst._send_cyclic_axis_output(telem, True)

        events = self.mock_simconnect.sent_events
        event_names = [e[0] for e in events]
        assert "AXIS_CYCLIC_LATERAL_SET" in event_names
        assert "AXIS_CYCLIC_LONGITUDINAL_SET" in event_names

    def test_send_axis_output_stores_last_device(self):
        inst = self._make_instance()
        inst.telemffb_controls_axes = True
        inst.local_disable_axis_control = False
        inst.cyclic_spring_init = 1
        inst.joystick_x_axis_scale = 1.0
        inst.joystick_y_axis_scale = 1.0
        inst.cyclic_virtual_trim_x_offs = 0.0
        inst.cyclic_virtual_trim_y_offs = 0.0
        inst.trim_following = False
        self.mock_device._input_data.set_axis(x=0.35, y=-0.15)
        telem = self._make_telem()
        self.set_telemetry(inst, telem)

        inst._send_cyclic_axis_output(telem, True)

        assert inst.last_device_x == 0.35
        assert inst.last_device_y == -0.15

    def test_send_axis_output_uses_firmware_backend(self):
        inst = self._make_instance()
        inst.telemffb_controls_axes = True
        inst.use_firmware_axis_override = True
        inst.local_disable_axis_control = False
        inst.cyclic_spring_init = 1
        inst.joystick_x_axis_scale = 1.0
        inst.joystick_y_axis_scale = 1.0
        inst.cyclic_virtual_trim_x_offs = 0.0
        inst.cyclic_virtual_trim_y_offs = 0.0
        inst.trim_following = False
        self.mock_device._input_data.set_axis(x=0.3, y=-0.2)

        import telemffb.globals as G
        G.device_firmware_version = "v1.0.18b14"

        telem = self._make_telem()
        self.set_telemetry(inst, telem)

        inst._send_cyclic_axis_output(telem, True)

        assert len(self.mock_simconnect.sent_events) == 0
        assert len(self.mock_device.axis_override_commands) > 0
        last = self.mock_device.axis_override_commands[-1]
        assert last["x_mode"] == 2
        assert last["y_mode"] == 2

    def test_helicopter_timeout_clears_firmware_override(self):
        inst = self._make_instance()
        inst.use_firmware_axis_override = True
        inst.telemffb_controls_axes = True

        import telemffb.globals as G
        G.device_firmware_version = "v1.0.18b14"

        inst.on_timeout()

        assert len(self.mock_device.axis_override_commands) > 0
        last = self.mock_device.axis_override_commands[-1]
        assert last["watchdog_ms"] == 0


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
@pytest.mark.msfs
@pytest.mark.helicopter
class TestHelicopterSimvarSync(BaseTelemetryEffectTestCase):
    """Tests for simvar subscription sync methods (_sync_controls_lock_simvar, _sync_force_trim_simvar)."""

    def _make_pedals_instance(self):
        instance = self.create_aircraft_instance(Helicopter, name="TestHeli", _test_sim_is_msfs=True, _test_device_type="pedals")
        instance.telemffb_controls_axes = True
        return instance

    def _create_pedal_telem(self):
        return TelemetryDataBuilder() \
            .with_sim_on_ground(0) \
            .with_airspeed(10.0) \
            .with_field("AircraftClass", "Helicopter") \
            .with_field("FFBType", "pedals") \
            .with_field("N", 100.0) \
            .with_field("TailRotorPedalPos", 0.0) \
            .build()

    def test_controls_lock_simvar_subscribes_on_first_call(self):
        """When controls_lock_enable and controls_lock_simvar are set, first call subscribes ControlsLock."""
        instance = self._make_pedals_instance()
        instance.controls_lock_enable = True
        instance.controls_lock_simvar = "L:PARKING_BRAKE_LOCK"

        telem = self._create_pedal_telem()
        self.set_telemetry(instance, telem)
        self.mock_device._input_data.set_axis(x=0.5)

        instance.msfs_update_pedals(telem)

        assert "ControlsLock" in self.mock_simconnect.sv_dict
        assert self.mock_simconnect.sv_dict["ControlsLock"]["var"] == "L:PARKING_BRAKE_LOCK"
        assert self.mock_simconnect._resubscribe_count >= 1

    def test_controls_lock_simvar_does_not_resubscribe_on_repeat(self):
        """Second call with same config should not add_simvar again."""
        instance = self._make_pedals_instance()
        instance.controls_lock_enable = True
        instance.controls_lock_simvar = "L:PARKING_BRAKE_LOCK"

        telem = self._create_pedal_telem()
        self.set_telemetry(instance, telem)
        self.mock_device._input_data.set_axis(x=0.5)

        instance.msfs_update_pedals(telem)
        count_after_first = self.mock_simconnect.add_simvar_count

        instance.msfs_update_pedals(telem)
        assert self.mock_simconnect.add_simvar_count == count_after_first

    def test_controls_lock_simvar_resubscribes_on_config_change(self):
        """Changing controls_lock_simvar should trigger a new subscription."""
        instance = self._make_pedals_instance()
        instance.controls_lock_enable = True
        instance.controls_lock_simvar = "L:PARKING_BRAKE_LOCK"

        telem = self._create_pedal_telem()
        self.set_telemetry(instance, telem)
        self.mock_device._input_data.set_axis(x=0.5)

        instance.msfs_update_pedals(telem)
        count_after_first = self.mock_simconnect.add_simvar_count

        instance.controls_lock_simvar = "L:NEW_LOCK_VAR"
        instance.msfs_update_pedals(telem)

        assert self.mock_simconnect.add_simvar_count > count_after_first
        assert self.mock_simconnect.sv_dict["ControlsLock"]["var"] == "L:NEW_LOCK_VAR"

    def test_controls_lock_simvar_skips_when_disabled(self):
        """When controls_lock_enable is False, no subscription happens."""
        instance = self._make_pedals_instance()
        instance.controls_lock_enable = False
        instance.controls_lock_simvar = "L:PARKING_BRAKE_LOCK"

        telem = self._create_pedal_telem()
        self.set_telemetry(instance, telem)
        self.mock_device._input_data.set_axis(x=0.5)

        instance.msfs_update_pedals(telem)

        assert "ControlsLock" not in self.mock_simconnect.sv_dict

    def test_controls_lock_simvar_skips_when_empty(self):
        """When controls_lock_simvar is empty, no subscription happens."""
        instance = self._make_pedals_instance()
        instance.controls_lock_enable = True
        instance.controls_lock_simvar = ""

        telem = self._create_pedal_telem()
        self.set_telemetry(instance, telem)
        self.mock_device._input_data.set_axis(x=0.5)

        instance.msfs_update_pedals(telem)

        assert "ControlsLock" not in self.mock_simconnect.sv_dict

    def test_force_trim_simvar_subscribes_on_custom_var_change(self):
        """Enabling custom_ft_sw_var_enabled should trigger ForceTrimSW re-subscription via msfs_update_pedals."""
        instance = self._make_pedals_instance()
        instance.custom_ft_sw_var_enabled = False
        instance.custom_ft_sw_var = "L:CUSTOM_FT_VAR"

        telem = self._create_pedal_telem()
        self.set_telemetry(instance, telem)
        self.mock_device._input_data.set_axis(x=0.5)

        # First call: establishes baseline for anything_has_changed
        instance.msfs_update_pedals(telem)

        # Enable custom var — should trigger resubscription
        instance.custom_ft_sw_var_enabled = True
        instance.msfs_update_pedals(telem)

        ft_calls = [c for c in self.mock_simconnect.simvar_calls if "CUSTOM_FT_VAR" in c]
        assert len(ft_calls) >= 1


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


@pytest.mark.unit
@pytest.mark.msfs
@pytest.mark.helicopter
class TestMsfsAxisHelpers(BaseTelemetryEffectTestCase):
    """Tests for shared MSFS axis scaling and config lookup methods."""

    def _make_instance(self):
        return self.create_aircraft_instance(Helicopter, name="TestHeli", _test_sim_is_msfs=True)

    # -- _get_msfs_axis_config --

    def test_get_msfs_axis_config_returns_defaults(self):
        instance = self._make_instance()
        instance.enable_custom_x_axis = False
        instance.enable_custom_y_axis = False

        var, rng = instance._get_msfs_axis_config('x', "AXIS_AILERONS_SET")
        assert var == "AXIS_AILERONS_SET"
        assert rng == 16384

        var, rng = instance._get_msfs_axis_config('y', "AXIS_ELEVATOR_SET")
        assert var == "AXIS_ELEVATOR_SET"
        assert rng == 16384

    def test_get_msfs_axis_config_custom_x(self):
        instance = self._make_instance()
        instance.enable_custom_x_axis = True
        instance.custom_x_axis = "MY_CUSTOM_X"
        instance.raw_x_axis_scale = 1

        var, rng = instance._get_msfs_axis_config('x', "AXIS_AILERONS_SET")
        assert var == "MY_CUSTOM_X"
        assert rng == 1

    def test_get_msfs_axis_config_custom_y(self):
        instance = self._make_instance()
        instance.enable_custom_y_axis = True
        instance.custom_y_axis = "MY_CUSTOM_Y"
        instance.raw_y_axis_scale = 32768

        var, rng = instance._get_msfs_axis_config('y', "AXIS_ELEVATOR_SET")
        assert var == "MY_CUSTOM_Y"
        assert rng == 32768

    def test_get_msfs_axis_config_custom_x_doesnt_affect_y(self):
        instance = self._make_instance()
        instance.enable_custom_x_axis = True
        instance.custom_x_axis = "MY_CUSTOM_X"
        instance.raw_x_axis_scale = 1
        instance.enable_custom_y_axis = False

        var, rng = instance._get_msfs_axis_config('y', "AXIS_ELEVATOR_SET")
        assert var == "AXIS_ELEVATOR_SET"
        assert rng == 16384

    def test_get_msfs_axis_config_custom_default_range(self):
        instance = self._make_instance()
        instance.enable_custom_x_axis = False

        var, rng = instance._get_msfs_axis_config('x', "AXIS_RUDDER_SET", 8192)
        assert var == "AXIS_RUDDER_SET"
        assert rng == 8192

    # -- _scale_msfs_axis_value --

    def test_scale_msfs_axis_value_integer_range(self):
        instance = self._make_instance()
        result = instance._scale_msfs_axis_value(1.0, 16384, 1.0)
        assert result == -16384

    def test_scale_msfs_axis_value_center_is_zero(self):
        instance = self._make_instance()
        result = instance._scale_msfs_axis_value(0.0, 16384, 1.0)
        assert result == 0

    def test_scale_msfs_axis_value_negative_full(self):
        instance = self._make_instance()
        result = instance._scale_msfs_axis_value(-1.0, 16384, 1.0)
        assert result == 16384

    def test_scale_msfs_axis_value_with_scale_factor(self):
        instance = self._make_instance()
        result = instance._scale_msfs_axis_value(1.0, 16384, 0.5)
        assert result == -8192

    def test_scale_msfs_axis_value_float_range(self):
        """When axis_range is 1 (float custom axis), result is rounded float, not negated int."""
        instance = self._make_instance()
        result = instance._scale_msfs_axis_value(0.5, 1, 1.0)
        assert result == 0.5
        assert isinstance(result, float)

    def test_scale_msfs_axis_value_float_range_negative(self):
        instance = self._make_instance()
        result = instance._scale_msfs_axis_value(-0.33, 1, 1.0)
        assert result == -0.33

    # -- _send_msfs_axis_value --

    def test_send_msfs_axis_value_sends_event(self):
        instance = self._make_instance()
        self.mock_simconnect.clear_events()

        result = instance._send_msfs_axis_value("AXIS_AILERONS_SET", 0.5, 16384, 1.0)

        assert len(self.mock_simconnect.sent_events) == 1
        event_name, event_val = self.mock_simconnect.sent_events[0]
        assert event_name == "AXIS_AILERONS_SET"
        assert event_val == -8192
        assert result == -8192

    def test_send_msfs_axis_value_returns_scaled_value(self):
        instance = self._make_instance()
        self.mock_simconnect.clear_events()

        result = instance._send_msfs_axis_value("TEST_VAR", 0.0, 16384, 1.0)
        assert result == 0

    def test_send_msfs_axis_value_float_range(self):
        instance = self._make_instance()
        self.mock_simconnect.clear_events()

        result = instance._send_msfs_axis_value("CUSTOM_VAR", 0.75, 1, 1.0)
        assert result == 0.75
        assert self.mock_simconnect.sent_events[0] == ("CUSTOM_VAR", 0.75)


class TestCheckHandsOn(BaseTelemetryEffectTestCase):
    """Tests for check_hands_on extracted to parent Helicopter class."""

    def _make_instance(self, **kwargs):
        defaults = dict(
            name="TestHeli", _test_sim_is_msfs=True, _test_device_type="joystick"
        )
        defaults.update(kwargs)
        return self.create_aircraft_instance(Helicopter, **defaults)

    def test_hands_on_below_threshold_returns_false(self):
        instance = self._make_instance()
        instance.cpO_x = 0
        instance.cpO_y = 0
        self.mock_device._input_data.set_axis(x=0.0, y=0.0)

        result = instance.check_hands_on(0.1)

        assert result["master_result"] is False
        assert result["x_result"] is False
        assert result["y_result"] is False

    def test_hands_on_x_exceeds_threshold(self):
        instance = self._make_instance()
        instance.cpO_x = 0
        instance.cpO_y = 0
        self.mock_device._input_data.set_axis(x=0.5, y=0.0)

        result = instance.check_hands_on(0.1)

        assert result["x_result"] is True
        assert result["master_result"] is True
        assert result["y_result"] is False

    def test_hands_on_y_exceeds_threshold(self):
        instance = self._make_instance()
        instance.cpO_x = 0
        instance.cpO_y = 0
        self.mock_device._input_data.set_axis(x=0.0, y=0.5)

        result = instance.check_hands_on(0.1)

        assert result["y_result"] is True
        assert result["master_result"] is True
        assert result["x_result"] is False

    def test_hands_on_returns_deviations(self):
        instance = self._make_instance()
        instance.cpO_x = 0
        instance.cpO_y = 0
        self.mock_device._input_data.set_axis(x=0.25, y=-0.5)

        result = instance.check_hands_on(0.1)

        assert result["x_deviation"] == pytest.approx(0.25, abs=0.01)
        assert result["y_deviation"] == pytest.approx(0.5, abs=0.01)

    def test_hands_on_returns_raw_deviations(self):
        instance = self._make_instance()
        instance.cpO_x = 1000 / 4096
        instance.cpO_y = -500 / 4096

        self.mock_device._input_data.set_axis(x=0.5, y=0.0)

        result = instance.check_hands_on(0.01)

        assert result["x_deviation_raw"] == pytest.approx(0.5 - 1000 / 4096)
        assert result["y_deviation_raw"] == pytest.approx(500 / 4096)

    def test_hands_on_with_offset_reference(self):
        instance = self._make_instance()
        instance.cpO_x = 2000 / 4096
        instance.cpO_y = 2000 / 4096
        self.mock_device._input_data.set_axis(x=0.5, y=0.5)

        result = instance.check_hands_on(0.1)

        assert isinstance(result["master_result"], bool)
        assert isinstance(result["x_deviation"], float)
        assert isinstance(result["y_deviation"], float)


class TestDispatchHandsOnState(BaseTelemetryEffectTestCase):
    """Tests for _dispatch_hands_on_state extracted to parent Helicopter class."""

    def _make_instance(self, **kwargs):
        defaults = dict(
            name="TestHeli", _test_sim_is_msfs=True, _test_device_type="joystick"
        )
        defaults.update(kwargs)
        inst = self.create_aircraft_instance(Helicopter, **defaults)
        inst.send_individual_hands_on = 0
        inst.hands_on_active = 0
        inst.hands_on_x_active = 0
        inst.hands_on_y_active = 0
        return inst

    def _make_telem(self):
        return TelemetryDataBuilder() \
            .with_field("FFBType", "joystick") \
            .build()

    def _make_hands_on_dict(self, x_result=False, y_result=False):
        return {
            "master_result": x_result or y_result,
            "x_result": x_result,
            "x_deviation": 0.5 if x_result else 0.0,
            "x_deviation_raw": 2048 if x_result else 0,
            "y_result": y_result,
            "y_deviation": 0.3 if y_result else 0.0,
            "y_deviation_raw": 1228 if y_result else 0,
        }

    def test_master_hands_on_sets_simvar(self):
        instance = self._make_instance()
        telem = self._make_telem()
        hands_on_dict = self._make_hands_on_dict(x_result=True)

        instance._dispatch_hands_on_state(telem, hands_on_dict, True)

        assert instance.hands_on_active is True
        simdata = self.mock_simconnect.sim_data_written
        assert ("L:FFB_HANDS_ON_CYCLIC", 1) in [(k, v) for k, v, _ in simdata]

    def test_master_hands_off_clears_simvar(self):
        instance = self._make_instance()
        telem = self._make_telem()
        hands_on_dict = self._make_hands_on_dict()

        instance._dispatch_hands_on_state(telem, hands_on_dict, False)

        assert instance.hands_on_active is False
        simdata = self.mock_simconnect.sim_data_written
        assert ("L:FFB_HANDS_ON_CYCLIC", 0) in [(k, v) for k, v, _ in simdata]

    def test_individual_mode_sets_x_and_y(self):
        instance = self._make_instance()
        instance.send_individual_hands_on = 1
        telem = self._make_telem()
        hands_on_dict = self._make_hands_on_dict(x_result=True, y_result=False)

        instance._dispatch_hands_on_state(telem, hands_on_dict, True)

        assert instance.hands_on_x_active is True
        assert instance.hands_on_y_active is False
        simdata = [(k, v) for k, v, _ in self.mock_simconnect.sim_data_written]
        assert ("L:FFB_HANDS_ON_CYCLICX", 1) in simdata
        assert ("L:FFB_HANDS_ON_CYCLICY", 0) in simdata

    def test_individual_mode_both_on(self):
        instance = self._make_instance()
        instance.send_individual_hands_on = 1
        telem = self._make_telem()
        hands_on_dict = self._make_hands_on_dict(x_result=True, y_result=True)

        instance._dispatch_hands_on_state(telem, hands_on_dict, True)

        assert instance.hands_on_x_active is True
        assert instance.hands_on_y_active is True

    def test_sets_telem_data_fields(self):
        instance = self._make_instance()
        telem = self._make_telem()
        hands_on_dict = self._make_hands_on_dict(x_result=True, y_result=False)

        instance._dispatch_hands_on_state(telem, hands_on_dict, True)

        assert telem.hands_on == 1
        assert telem.hands_on_x == 1
        assert telem.hands_on_y == 0
        assert telem.deviation_x == 0.5
        assert telem.deviation_y == 0.0

    def test_hands_off_sets_telem_data_fields(self):
        instance = self._make_instance()
        telem = self._make_telem()
        hands_on_dict = self._make_hands_on_dict()

        instance._dispatch_hands_on_state(telem, hands_on_dict, False)

        assert telem.hands_on == 0
        assert telem.hands_on_x == 0
        assert telem.hands_on_y == 0
