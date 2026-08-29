"""
Base classes and mocks for telemetry effect testing.
"""
from typing import Dict, List, Optional, Any, TypeVar, Type
from unittest.mock import MagicMock, Mock
import telemffb.globals as G
from telemffb.sim.BaseTelemetryData import BaseTelemetryData

T = TypeVar('T')

class MockInputData:
    """Mock for FFB device input data."""
    
    def __init__(self):
        self._x_axis = 0.0
        self._y_axis = 0.0
        self._raw_x_axis = 0.0
        self._raw_y_axis = 0.0
        self._buttons = set()
        self.status = 0
    
    def axisXY(self):
        """Return current X and Y axis values."""
        return (self._x_axis, self._y_axis)

    def rawAxisXY(self):
        """Return current raw X and Y axis values."""
        return (self._raw_x_axis, self._raw_y_axis)

    def axisOverrideActive(self):
        return bool(self.status & 0x03)
    
    def CP_scaled_axisXY(self):
        """Return current X and Y axis values scaled for center position."""
        return (self._x_axis, self._y_axis)
    
    def forceXY(self):
        """Return current force values (mock returns zeros)."""
        return (0.0, 0.0)
    
    def set_axis(self, x: float = None, y: float = None):
        """Set axis values for testing."""
        if x is not None:
            self._x_axis = x
            self._raw_x_axis = x
        if y is not None:
            self._y_axis = y
            self._raw_y_axis = y

    def set_raw_axis(self, x: float = None, y: float = None):
        if x is not None:
            self._raw_x_axis = x
        if y is not None:
            self._raw_y_axis = y
    
    def isButtonPressed(self, button: int) -> bool:
        """Check if button is pressed."""
        return button in self._buttons
    
    def getPressedButtons(self) -> list:
        """Get list of pressed buttons."""
        return list(self._buttons)
    
    def press_button(self, button: int):
        """Simulate button press."""
        self._buttons.add(button)
    
    def release_button(self, button: int):
        """Simulate button release."""
        self._buttons.discard(button)


class MockFFBDevice:
    """Mock FFB device for testing."""

    def __init__(self):
        self._input_data = MockInputData()
        self._connected = True
        self.axis_override_commands = []
        # full native capability set: existing tests model the VPforce device
        from telemffb.hw.ffb_backend import VPFORCE_CAPABILITIES
        self.caps = VPFORCE_CAPABILITIES
    

    @property
    def connected(self) -> bool:
        return self._connected

    def set_connected(self, value: bool):
        self._connected = value

    def get_input(self) -> MockInputData:
        """Return mock input data."""
        return self._input_data
    
    def create_effect(self, effect_type):
        """Create a mock effect."""
        return MockConditionEffect()

    def supports_axis_override(self):
        import telemffb.globals as G
        import re
        if G.device_firmware_version is None:
            return False
        # v1.0.18b14 -> 101814, v1.0.18 -> 1018
        # Use numeric comparison to ensure beta versions (>1018) pass
        ver = int(re.sub(r'\D', '', G.device_firmware_version))
        return ver > 1018

    def send_axis_override(self, x_mode=0, x_value=0, y_mode=0, y_value=0, watchdog_ms=1000):
        self.axis_override_commands.append(
            {
                "x_mode": x_mode,
                "x_value": x_value,
                "y_mode": y_mode,
                "y_value": y_value,
                "watchdog_ms": watchdog_ms,
            }
        )

    def clear_axis_override(self):
        self.axis_override_commands.append({
            "x_mode": 0,
            "x_value": 0,
            "y_mode": 0,
            "y_value": 0,
            "watchdog_ms": 0,
        })


class MockConditionEffect:
    """Mock for condition effect (spring, damper, friction, inertia)."""
    effect_id = 0
    def __init__(self, name: str = ""):
        self.name = name
        self.started = False
        self.effect_type = 0  # Default effect type
        self.detent_config = None
        self._envelope = None
        self._envelope_once = False
        self._x_coefficient = 0
        self._y_coefficient = 0
        self._x_offset = 0
        self._y_offset = 0
        self._x_saturation = 0
        self._y_saturation = 0
        self.start_count = 0
        self.stop_count = 0
        self.destroy_count = 0
        self.effect_id += 1

    
    def start(self, override=False):
        """Start the effect.
        
        Args:
            override: Whether to override existing effects (ignored in mock)
        """
        self.started = True
        self.start_count += 1
        return self
    
    def stop(self):
        """Stop the effect."""
        self.started = False
        if self._envelope_once:
            self._envelope = None
            self._envelope_once = False
        self.stop_count += 1
        return self
    
    def destroy(self):
        """Destroy the effect."""
        self.started = False
        self.destroy_count += 1
        return self
    
    def spring(self, x_coeff: int = 0, y_coeff: int = 0):
        """Set spring parameters."""
        self._x_coefficient = x_coeff
        self._y_coefficient = y_coeff
        return self
    
    def damper(self, x_coeff: int = 0, y_coeff: int = 0):
        """Set damper parameters."""
        self._x_coefficient = x_coeff
        self._y_coefficient = y_coeff
        return self
    
    def friction(self, x_coeff: int = 0, y_coeff: int = 0):
        """Set friction parameters."""
        self._x_coefficient = x_coeff
        self._y_coefficient = y_coeff
        return self
    
    def inertia(self, x_coeff: int = 0, y_coeff: int = 0):
        """Set inertia parameters."""
        self._x_coefficient = x_coeff
        self._y_coefficient = y_coeff
        return self
    
    def constant(self, magnitude: float, direction: float = 0):
        """Set constant force parameters."""
        # Store as magnitude/direction (polar form)
        self._magnitude = magnitude
        self._direction = direction
        return self

    def periodic(self, frequency=0, magnitude=0, direction=0, *args, **kwargs):
        """Set periodic effect parameters (rumble/vibration; chainable)."""
        self._periodic = (frequency, magnitude, direction, kwargs)
        return self
    
    def setEffect(self):
        """Mock setEffect method."""
        return self
    
    def setCondition(self, condition):
        """Set condition from condition object."""
        if hasattr(condition, 'cpOffset'):
            if condition.parameterBlockOffset == 0:  # X axis
                self._x_offset = condition.cpOffset
                self._x_coefficient = condition.positiveCoefficient or condition.negativeCoefficient
                self._x_saturation = condition.positiveSaturation or condition.negativeSaturation
            elif condition.parameterBlockOffset == 1:  # Y axis
                self._y_offset = condition.cpOffset
                self._y_coefficient = condition.positiveCoefficient or condition.negativeCoefficient
                self._y_saturation = condition.positiveSaturation or condition.negativeSaturation
        return self
    
    def setConstantForce(self, magnitude: float, direction: float = 0):
        """Set constant force parameters."""
        self._magnitude = magnitude
        self._direction = direction
        return self

    def detent(self, **kwargs):
        """Store detent configuration and return self for call chaining."""
        self.effect_type = 11  # EFFECT_DETENT in production code
        self.detent_config = kwargs.copy()
        return self

    def envelope(self, attackFromForce=None, decayToForce=None, attackTime=None, decayTime=None, once=False, **kwargs):
        """Store envelope parameters and return self for call chaining."""
        if 'envelope' in kwargs:
            self._envelope = kwargs['envelope']
        else:
            params = {}
            if attackFromForce is not None:
                params['attackFromForce'] = attackFromForce
            if decayToForce is not None:
                params['decayToForce'] = decayToForce
            if attackTime is not None:
                params['attackTime'] = attackTime
            if decayTime is not None:
                params['decayTime'] = decayTime
            self._envelope = params if params else None
        self._envelope_once = once
        return self

    def setEnvelope(self, envelope):
        """Set envelope object directly to mirror production effect API."""
        self._envelope = envelope
        return self
    
    def get_coefficients(self):
        """Get current coefficients."""
        return (self._x_coefficient, self._y_coefficient)
    
    def get_offsets(self):
        """Get current offsets."""
        return (self._x_offset, self._y_offset)
    
    def spring_adjuster(self, *args, **kwargs):
        """Return self for spring adjuster chain. Accept args/kwargs to match production signature."""
        return self
    
    def reset_counters(self):
        """Reset call counters for testing."""
        self.start_count = 0
        self.stop_count = 0
        self.destroy_count = 0


class MockHapticEffect:
    """Mock haptic effect container."""
    
    device = None  # Class variable for device
    effect_id = 0
    
    def __init__(self, device: MockFFBDevice = None):
        if device is None:
            device = MockFFBDevice()
        MockHapticEffect.device = device
        self.effects = {}
        self.effect_id += 1

    def get_effect(self, name: str) -> MockConditionEffect:
        """Get or create an effect by name."""
        if name not in self.effects:
            self.effects[name] = MockConditionEffect(name)
        return self.effects[name]


class MockEffectDispenser:
    """Mock for the global effects dispenser."""
    
    def __init__(self):
        self._effects = {}
    
    @property
    def dict(self):
        """Return the internal effects dictionary."""
        return self._effects
    
    def __getitem__(self, key: str) -> MockConditionEffect:
        """Get effect by name."""
        if key not in self._effects:
            self._effects[key] = MockConditionEffect(key)
        return self._effects[key]

    def __contains__(self, key: str) -> bool:
        """Mirror the real Dispenser: membership checks the dict and must
        not fall back to the legacy __getitem__(0), (1), ... iteration
        protocol, which never terminates on a create-on-access mapping."""
        return key in self._effects
    
    def get(self, key: str, default=None):
        """Get effect by name with optional default."""
        if key in self._effects:
            return self._effects[key]
        return default
    
    def clear(self):
        """Clear all effects."""
        self._effects.clear()

    def values(self):
        """Return all effect values."""
        return self._effects.values()

    def reset_all(self):
        """Reset all effects."""
        for effect in self._effects.values():
            effect.stop()
            effect.reset_counters()

    def dispose(self, *args):
        for name in args:
            if name in self._effects:
                del self._effects[name]


class MockSimConnect:
    """Mock SimConnect for MSFS integration testing."""
    
    def __init__(self):
        self.sent_events = []
        self.variables = {}
        self.sv_dict = {}
        self.simvar_calls = []
        self.add_simvar_count = 0
        self._resubscribe_count = 0
        self.sim_data_written = []
    
    def send_event_to_msfs(self, event_name: str, value: Any):
        """Record sent events."""
        self.sent_events.append((event_name, value))
    
    def set_simdatum_to_msfs(self, simvar: str, value: Any, units: str = ""):
        """Record simvar writes."""
        self.sim_data_written.append((simvar, value, units))
    
    def set_variable(self, var_name: str, value: Any):
        """Set a variable."""
        self.variables[var_name] = value
    
    def get_variable(self, var_name: str) -> Any:
        """Get a variable."""
        return self.variables.get(var_name)
    
    def add_simvar(self, name: str, var: str, sc_unit: str = ""):
        """Mock adding a simvar."""
        self.add_simvar_count += 1
        self.simvar_calls.append(f"add_simvar: {name} = {var} ({sc_unit})")
        self.sv_dict[name] = {"var": var, "unit": sc_unit}
    
    def _resubscribe(self):
        """Mock resubscribe."""
        self._resubscribe_count += 1
    
    def clear_events(self):
        """Clear recorded events."""
        self.sent_events.clear()
    
    def get_last_event(self) -> Optional[tuple]:
        """Get the last sent event."""
        return self.sent_events[-1] if self.sent_events else None


class MockDampener:
    """Mock for the dampener utility."""
    
    def __init__(self):
        self._values = {}
    
    def dampen_value(self, value, key, derivative_hz=5, derivative_k=0.15):
        """Simple pass-through dampening for tests."""
        self._values[key] = value
        return value
    
    def update(self, value, derivative_hz=5, derivative_k=0.15):
        """Simple pass-through update for tests."""
        return value


class MockSpringCondition:
    """Mock for FFBReport_SetCondition used for spring configuration."""
    
    def __init__(self, parameterBlockOffset=0):
        self.parameterBlockOffset = parameterBlockOffset
        self.cpOffset = 0
        self.positiveCoefficient = 0
        self.negativeCoefficient = 0
        self.positiveSaturation = 0
        self.negativeSaturation = 0
    
    def set_coefficient(self, coefficient, override=False):
        """Set spring coefficient."""
        self.positiveCoefficient = coefficient
        self.negativeCoefficient = coefficient
    
    def set_offset(self, offset):
        """Set spring offset."""
        self.cpOffset = int(offset * 4096) if abs(offset) <= 1 else int(offset)


class BaseTelemetryEffectTestCase:
    """Base test case class for telemetry effects.
    
    Provides common setup and utilities for testing effect mixins.
    """
    
    def setup_method(self):
        """Setup before each test method."""
        # Create mocks
        self.mock_device = MockFFBDevice()
        self.mock_haptic = MockHapticEffect(self.mock_device)
        self.mock_simconnect = MockSimConnect()
        self.mock_effects = MockEffectDispenser()
        
        # Setup globals
        G.effects = self.mock_effects
        G.master_buttons = []
        
        # Store original HapticEffect.device
        from telemffb.hw.ffb_rhino import HapticEffect
        self._original_haptic_device = HapticEffect.device
        HapticEffect.device = self.mock_device
    
    def teardown_method(self):
        """Cleanup after each test method."""
        # Restore original device
        from telemffb.hw.ffb_rhino import HapticEffect
        HapticEffect.device = self._original_haptic_device
        
        # Clear globals
        G.effects = None
        G.master_buttons = []
    
    def create_test_instance(self, mixin_class: Type[T], **kwargs) -> T:
        """Create a test instance of a mixin with proper base class chain.
        
        Args:
            mixin_class: The mixin class to test
            **kwargs: Additional initialization parameters
        
        Returns:
            Instance of the mixin configured for testing
        """
        # Create a concrete test class that includes all required base classes
        class TestClass(mixin_class):
            def __init__(self, **init_kwargs):
                # Initialize state that would normally come from base classes
                self._changes = {}
                self._change_counter = {}
                self._ipc_telem = {}
                self.stepper_dict = {}
                self.spring_mode = None
                self.gforce_effect_mode = None
                self._telem_data = BaseTelemetryData()
                self._last_telem_data = BaseTelemetryData()
                self.friction_effect_overridden = False
                self.telemffb_controls_axes = False
                self.local_disable_axis_control = False
                self.dampener = MockDampener()
                self.elev_trim_dampener = MockDampener()
                self.aileron_pos_dampener = MockDampener()
                self.mock_simconnect = None
                # Initialize spring conditions
                self.spring_x = MockSpringCondition(parameterBlockOffset=0)
                self.spring_y = MockSpringCondition(parameterBlockOffset=1)
                self.__spring_handle_instance = None
                # Initialize force trim
                self.force_trim_x_offset = 0
                self.force_trim_y_offset = 0
                # Initialize other required attributes
                self.ap_following = False
                self.aoa_effect_enabled = 0
                self.aoa_effect_gain = 0.5
                # self.steering_friction = 0
                # self.steering_friction_spring = 0
                # Trim-following gains mirror the SHIPPED defaults
                # (defaults.xml), not zero: a test that does not set them
                # should exercise the configuration users actually fly.
                # Zeroes silently scaled trim-following contributions to
                # nothing, which read as passing tests for the wrong reason.
                self.joystick_trim_follow_gain_physical_x = 1.0
                self.joystick_trim_follow_gain_physical_y = 1.0
                self.joystick_trim_follow_gain_virtual_x = 0.2
                self.joystick_trim_follow_gain_virtual_y = 0.2
                self.rudder_trim_follow_gain_physical_x = 1.0
                self.rudder_trim_follow_gain_virtual_x = 0.2
                self.joystick_x_axis_scale = 1.0
                self.joystick_y_axis_scale = 1.0
                self.rudder_x_axis_scale = 1.0
                self.enable_custom_x_axis = False
                self.enable_custom_y_axis = False
                self.custom_x_axis = ""
                self.custom_y_axis = ""
                self.raw_x_axis_scale = 16384
                self.raw_y_axis_scale = 16384
                self.adv_spr_gains = "none"
                # Initialize with parent
                super().__init__()
                
                # Apply any custom kwargs
                for key, value in init_kwargs.items():
                    setattr(self, key, value)

            def on_telemetry(self, telem_data):
                """Mock on_telemetry method."""
                self._telem_data = telem_data.copy()
                self._last_telem_data = telem_data.copy()
                super().on_telemetry(telem_data)

            @property
            def _simconnect(self):
                """Mock SimConnect property."""
                if not self.mock_simconnect:
                    self.mock_simconnect = MockSimConnect()
                return self.mock_simconnect
            
            @_simconnect.setter
            def _simconnect(self, value):
                """Mock SimConnect setter."""
                self.mock_simconnect = value
            
            def _sim_is_msfs(self):
                """Mock sim check."""
                return getattr(self, '_test_sim_is_msfs', False)
            
            def _sim_is_xplane(self):
                """Mock sim check."""
                return getattr(self, '_test_sim_is_xplane', False)
            
            def is_pedals(self):
                """Mock pedals check."""
                return self._telem_data.get("FFBType", "") == "pedals"
            
            def is_joystick(self):
                """Mock joystick check."""
                return self._telem_data.get("FFBType", "") == "joystick"
            
            def flag_error(self, message):
                """Mock error flagging."""
                self._flagged_errors = getattr(self, '_flagged_errors', [])
                self._flagged_errors.append(message)
            
            def anything_has_changed(self, key, value):
                """Mock change detection."""
                changed = self._changes.get(key) != value
                self._changes[key] = value
                return changed
            
            def send_xp_command(self, command):
                """Mock XP command."""
                self._xp_commands = getattr(self, '_xp_commands', [])
                self._xp_commands.append(command)
            
            def spring_mode_is(self, mode):
                """Check if spring mode matches."""
                return self.spring_mode == mode
            
            @property
            def effects(self):
                """Return mock effects dispenser."""
                return G.effects
            
            @property
            def _spring_handle(self):
                """Return mock spring handle."""
                if self.__spring_handle_instance is None:
                    self.__spring_handle_instance = G.effects['dynamic_spring']
                    self.__spring_handle_instance.name = 'dynamic_spring'
                return self.__spring_handle_instance
        
        return TestClass(**kwargs)
    
    def create_aircraft_instance(self, aircraft_class: Type[T], name: str = "TestAircraft", **kwargs) -> T:
        """Create an aircraft instance with proper setup for testing.
        
        Args:
            aircraft_class: The aircraft class to instantiate
            name: Aircraft name (positional arg for Aircraft classes)
            **kwargs: Additional parameters for the aircraft
            
        Returns:
            Configured aircraft instance ready for testing
        """
        # Extract test-specific kwargs
        test_sim_is_msfs = kwargs.pop('_test_sim_is_msfs', False)
        test_sim_is_xplane = kwargs.pop('_test_sim_is_xplane', False)
        test_device_type = kwargs.pop('_test_device_type', 'joystick')
        
        # Setup G.telem_manager to avoid AttributeError
        # Always update simconnect to match this test's mock
        if not hasattr(G, 'telem_manager') or G.telem_manager is None:
            # Create a simple mock telem_manager
            telem_manager_mock = Mock()
            G.telem_manager = telem_manager_mock
        
        # Always set simconnect to this test's mock_simconnect
        G.telem_manager.simconnect = self.mock_simconnect
        
        # Create the instance with name as positional arg and pass remaining kwargs
        instance = aircraft_class(name, **kwargs)
        
        # Set up mock SimConnect if needed
        if not hasattr(instance, 'mock_simconnect') or instance.mock_simconnect is None:
            instance.mock_simconnect = self.mock_simconnect
        
        # Initialize telemetry data dict if not present
        if not hasattr(instance, '_telem_data'):
            instance._telem_data = BaseTelemetryData()
        
        # Setup test helpers for sim detection
        instance._test_sim_is_msfs = test_sim_is_msfs
        instance._test_sim_is_xplane = test_sim_is_xplane
        instance._test_device_type = test_device_type
        
        # Override sim detection methods
        original_sim_is_msfs = instance._sim_is_msfs
        original_sim_is_xplane = instance._sim_is_xplane
        
        def mock_sim_is_msfs():
            if hasattr(instance, '_test_sim_is_msfs'):
                return instance._test_sim_is_msfs
            return original_sim_is_msfs()
        
        def mock_sim_is_xplane():
            if hasattr(instance, '_test_sim_is_xplane'):
                return instance._test_sim_is_xplane
            return original_sim_is_xplane()
        
        instance._sim_is_msfs = mock_sim_is_msfs
        instance._sim_is_xplane = mock_sim_is_xplane
        
        return instance
    
    def set_telemetry(self, instance, telem_data):
        """Set telemetry data on an instance."""
        if isinstance(telem_data, BaseTelemetryData):
            instance._telem_data = telem_data.copy()
        else:
            instance._telem_data = BaseTelemetryData(telem_data)
    
    def assert_effect_started(self, effect_name: str, msg: str = ""):
        """Assert that an effect was started."""
        effect = self.mock_effects[effect_name]
        assert effect.started, f"Effect '{effect_name}' was not started. {msg}"
    
    def assert_effect_stopped(self, effect_name: str, msg: str = ""):
        """Assert that an effect was stopped."""
        effect = self.mock_effects[effect_name]
        assert not effect.started, f"Effect '{effect_name}' is still started. {msg}"
    
    def assert_effect_not_modified(self, effect_name: str, msg: str = ""):
        """Assert that an effect was not modified."""
        effect = self.mock_effects[effect_name]
        assert effect.start_count == 0, f"Effect '{effect_name}' was modified. {msg}"
