# TelemFFB Testing Framework

This directory contains comprehensive unit tests for the TelemFFB telemetry effects system.

## Overview

The testing framework provides:

- **Mock objects** for FFB devices, SimConnect, and telemetry data
- **Base test classes** for consistent test structure
- **Utility functions** for creating test data and assertions
- **Comprehensive test coverage** for telemetry effects

## Quick Start

### Installation

Make sure you have pytest installed:

```powershell
pip install pytest pytest-cov
```

### Running Tests

Run all tests:
```powershell
pytest
```

Run tests with verbose output:
```powershell
pytest -v
```

Run specific test file:
```powershell
pytest tests/test_steering_friction_effect.py
```

Run specific test class:
```powershell
pytest tests/test_fbw_flight_controls.py::TestMsfsXpFBWFlightControlsJoystick
```

Run specific test:
```powershell
pytest tests/test_steering_friction_effect.py::TestMfsfXpSteeringFrictionEffect::test_effect_disabled_by_default
```

### Running with Coverage

```powershell
pytest --cov=telemffb --cov-report=html
```

Then open `htmlcov/index.html` in your browser.

## Framework Components

### Base Classes (`tests/framework/base.py`)

#### `MockHapticEffect`
Mock FFB device for testing force feedback effects.

```python
mock_device = MockFFBDevice()
mock_haptic = MockHapticEffect(mock_device)
```

#### `MockSimConnect`
Mock SimConnect for testing MSFS integration.

```python
mock_simconnect = MockSimConnect()
mock_simconnect.send_event_to_msfs("AXIS_AILERONS_SET", 8192)
assert mock_simconnect.get_last_event() == ("AXIS_AILERONS_SET", 8192)
```

#### `MockEffectDispenser`
Mock global effects dispenser.

```python
mock_effects = MockEffectDispenser()
effect = mock_effects['friction']
effect.friction(2048, 2048).start()
```

#### `BaseTelemetryEffectTestCase`
Base class for effect tests with common setup.

```python
class TestMyEffect(BaseTelemetryEffectTestCase):
    def test_something(self):
        instance = self.create_test_instance(MyEffectMixin)
        # ... test code ...
```

### Utilities (`tests/framework/utils.py`)

#### `TelemetryDataBuilder`
Fluent interface for creating test telemetry data.

```python
telem = (
    TelemetryDataBuilder()
    .ffb_type("pedals")
    .on_ground()
    .ground_speed(15.0)
    .surface_type("Asphalt")
    .build()
)
```

#### Assertion Functions

```python
# Assert effect state
assert_effect_started(effect, "Effect should be running")
assert_effect_stopped(effect, "Effect should be stopped")

# Assert coefficients
assert_friction_coefficient_in_range(effect, 1000, 3000, axis="x")
assert_spring_coefficient_in_range(effect, 0, 4096)

# Assert SimConnect events
assert_simconnect_event_sent(mock_simconnect, "AXIS_RUDDER_SET")
assert_simconnect_event_value(mock_simconnect, "AXIS_RUDDER_SET", -8192, tolerance=100)
```

## Writing New Tests

### 1. Create Test File

Create a new file in `tests/` directory:

```python
"""
Tests for MyNewEffect.
"""
import pytest
from tests.framework.base import BaseTelemetryEffectTestCase
from tests.framework.utils import TelemetryDataBuilder
from telemffb.sim.my_module.MyNewEffect import MyNewEffect


class TestMyNewEffect(BaseTelemetryEffectTestCase):
    """Test suite for my new effect."""
    
    def test_basic_functionality(self):
        # Arrange
        instance = self.create_test_instance(MyNewEffect)
        instance._test_sim_is_msfs = True
        
        telem = TelemetryDataBuilder().ffb_type("joystick").build()
        
        # Act
        instance.on_telemetry(telem)
        
        # Assert
        effect = self.mock_effects['my_effect']
        assert effect.started, "Effect should be active"
```

### 2. Use Test Markers

```python
@pytest.mark.unit
def test_calculation(self):
    pass

@pytest.mark.msfs
def test_msfs_specific(self):
    pass

@pytest.mark.slow
def test_lengthy_operation(self):
    pass
```

### 3. Parametrize Tests

```python
@pytest.mark.parametrize("speed,expected_friction", [
    (0.0, 4096),    # Stationary = max friction
    (10.0, 2500),   # Medium speed
    (20.0, 1000),   # High speed = low friction
])
def test_friction_at_various_speeds(self, speed, expected_friction):
    instance = self.create_test_instance(MyEffect)
    telem = TelemetryDataBuilder().ground_speed(speed).build()
    instance.on_telemetry(telem)
    effect = self.mock_effects['friction']
    coeff = effect.get_coefficients()[0]
    assert abs(coeff - expected_friction) < 200
```

## Test Structure

Each test should follow the **Arrange-Act-Assert** pattern:

```python
def test_example(self):
    # Arrange - Set up test conditions
    instance = self.create_test_instance(MyEffect)
    instance.my_parameter = 1.0
    telem = TelemetryDataBuilder().build()
    
    # Act - Execute the code under test
    instance.on_telemetry(telem)
    
    # Assert - Verify the results
    effect = self.mock_effects['my_effect']
    assert effect.started
```

## Common Testing Patterns

### Testing Effect Activation

```python
def test_effect_activates_when_enabled(self):
    instance = self.create_test_instance(MyEffect)
    instance.my_effect_enabled = True
    
    telem = TelemetryDataBuilder().build()
    instance.on_telemetry(telem)
    
    assert_effect_started(self.mock_effects['my_effect'])
```

### Testing Parameter Scaling

```python
def test_parameter_scales_effect(self):
    instance = self.create_test_instance(MyEffect)
    telem = TelemetryDataBuilder().build()
    
    # Test with low parameter
    instance.my_gain = 0.2
    instance.on_telemetry(telem)
    low_coeff = self.mock_effects['my_effect'].get_coefficients()[0]
    
    # Test with high parameter
    instance.my_gain = 1.0
    instance.on_telemetry(telem)
    high_coeff = self.mock_effects['my_effect'].get_coefficients()[0]
    
    assert high_coeff > low_coeff
```

### Testing Cleanup

```python
def test_effect_cleans_up_when_disabled(self):
    instance = self.create_test_instance(MyEffect)
    instance.my_effect_enabled = True
    telem = TelemetryDataBuilder().build()
    
    # Activate effect
    instance.on_telemetry(telem)
    effect = self.mock_effects['my_effect']
    assert effect.started
    
    # Disable and verify cleanup
    instance.my_effect_enabled = False
    instance.on_telemetry(telem)
    assert effect.destroy_count > 0
```

### Testing SimConnect Integration

```python
def test_sends_axis_to_msfs(self):
    instance = self.create_test_instance(MyEffect)
    instance._simconnect = self.mock_simconnect
    instance.telemffb_controls_axes = True
    
    self.mock_device._input_data.set_axis(x=0.5, y=0.5)
    telem = TelemetryDataBuilder().ffb_type("joystick").build()
    
    instance.on_telemetry(telem)
    
    assert_simconnect_event_sent(self.mock_simconnect, "AXIS_AILERONS_SET")
    assert_simconnect_event_sent(self.mock_simconnect, "AXIS_ELEVATOR_SET")
```

## Mocking Simulator Type

```python
def test_msfs_only_effect(self):
    instance = self.create_test_instance(MyEffect)
    instance._test_sim_is_msfs = True  # Mock as MSFS
    # ... test MSFS-specific behavior ...

def test_xplane_only_effect(self):
    instance = self.create_test_instance(MyEffect)
    instance._test_sim_is_xplane = True  # Mock as X-Plane
    # ... test X-Plane-specific behavior ...
```

## Best Practices

1. **One concept per test** - Each test should verify one specific behavior
2. **Clear test names** - Use descriptive names that explain what is being tested
3. **Isolation** - Tests should not depend on each other
4. **Use fixtures** - Leverage pytest fixtures for common setup
5. **Mock external dependencies** - Don't test actual FFB hardware or simulators
6. **Test edge cases** - Include tests for boundary conditions and error cases
7. **Keep tests fast** - Unit tests should run quickly
8. **Document complex tests** - Add comments explaining non-obvious test logic

## Continuous Integration

To run tests automatically on code changes:

```powershell
# Watch for changes and re-run tests
pytest --watch
```

Or set up a pre-commit hook:

```powershell
# .git/hooks/pre-commit
#!/bin/sh
pytest --tb=short
```

## Troubleshooting

### Import Errors

If you get import errors, make sure the parent directory is in your Python path:

```powershell
$env:PYTHONPATH = "C:\Users\walmis\Desktop\Programming\VPforce-TelemFFB"
pytest
```

### Mock Not Working

Ensure you're using the test framework's mocks:

```python
# Correct
instance._simconnect = self.mock_simconnect

# Wrong - won't be tracked
instance._simconnect = SimConnect()
```

### Tests Pass But Code Fails

This usually means:
1. Mocks don't accurately represent real behavior
2. Integration between components isn't tested
3. Edge cases aren't covered

Add integration tests or check your mocks.

## Contributing

When adding new effects:

1. Write tests for the effect
2. Use the existing framework
3. Add new utility functions if needed
4. Update this README with new patterns
5. Ensure all tests pass before committing

## Examples

See the existing test files for comprehensive examples:

- `test_steering_friction_effect.py` - Effect lifecycle, parameter scaling, cleanup
- `test_fbw_flight_controls.py` - Complex effects with multiple modes and simulators

## License

Same as main TelemFFB project - see COPYING.txt
