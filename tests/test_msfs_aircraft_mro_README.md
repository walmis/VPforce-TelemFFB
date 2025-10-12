# MRO (Method Resolution Order) Chain Test

## Purpose

This test verifies that the `msfs_xp.Aircraft` class properly implements cooperative multiple inheritance by ensuring all mixin classes in the MRO chain have their lifecycle methods (`on_telemetry`, `on_event`, `on_timeout`) called via `super()`.

## Test Results Summary

### ✅ PASSED: `on_event` 
All 3 classes properly call `super().on_event()`
- Aircraft
- AircraftBase  
- AircraftEffectUtilsBase

### ✅ PASSED: `on_timeout`
All 6 classes properly call `super().on_timeout()`
- Aircraft
- AircraftBase
- DeadzoneMixIn
- MsfsXpFlightControlsMixIn
- MsfsXpFBWFlightControlsMixIn
- AircraftEffectUtilsBase

### ❌ FAILED: `on_telemetry`
**CRITICAL BUG FOUND**: `PedalSpringOverrideMixIn.on_telemetry()` breaks the MRO chain

## The Bug

`PedalSpringOverrideMixIn.on_telemetry()` contains:

```python
def on_telemetry(self, telem_data: dict):
    if not self.is_pedals(): return  # ❌ EARLY RETURN WITHOUT super()
    
    if not (self._sim_is_msfs() or self._sim_is_xplane()):
        self.ac_override_pedal_spring(telem_data)
```

This early return prevents **16+ mixin classes** from ever executing their telemetry handlers when the device is not pedals (i.e., joystick or collective).

## Impact

When using a joystick or collective, the following effects are **never processed**:
- HelicopterEffectsMixIn (ETL, VRS, engine rumble)
- WeaponsEffectMixIn (gunfire, weapons release)
- DeadzoneMixIn (deadzone handling)
- HydraulicLossMixIn (hydraulic loss effects)
- MsfsXpTrimwheelMixIn (trim wheel support)
- MfsfXpSteeringFrictionEffectMixIn (steering friction)
- DecelerationEffectMixIn (deceleration forces)
- EngineRumbleMixIn (engine rumble)
- WindEffectMixIn (wind effects)
- MsfsXpNosewheelShimmyMixIn (nosewheel shimmy)
- MsfsXpFlightControlsMixIn (flight control axis)
- AoAEffectsMixIn (angle of attack effects)
- AdvancedSpringMixIn (advanced spring override)
- GForceEffectMixIn (g-force effects)
- MotionEffectsMixIn (runway rumble, gear/flaps motion)
- BuffetingEffectMixIn (buffeting effects)
- AircraftEffectUtilsBase (base telemetry processing)

## The Fix

The mixin should call `super()` **before** any early returns:

```python
def on_telemetry(self, telem_data: dict):
    super().on_telemetry(telem_data)  # ✅ Call super FIRST
    
    if not self.is_pedals(): 
        return  # Now safe to return early
    
    if not (self._sim_is_msfs() or self._sim_is_xplane()):
        self.ac_override_pedal_spring(telem_data)
```

## Complete MRO Chain

The full inheritance hierarchy for `msfs_xp.Aircraft`:

```
 1. Aircraft                                  [on_telemetry, on_timeout, on_event]
 2. AircraftBase                              [on_telemetry, on_timeout, on_event]
 3. PedalSpringOverrideMixIn                  [on_telemetry] ⚠️ BREAKS CHAIN
 4. HelicopterEffectsMixIn                    [on_telemetry]
 5. WeaponsEffectMixIn                        [on_telemetry]
 6. DeadzoneMixIn                             [on_telemetry, on_timeout]
 7. HydraulicLossMixIn                        [on_telemetry]
 8. MsfsXpTrimwheelMixIn                      [on_telemetry]
 9. MfsfXpSteeringFrictionEffectMixIn         [on_telemetry]
10. FFBForcesMixIn
11. DecelerationEffectMixIn                   [on_telemetry]
12. EngineRumbleMixIn                         [on_telemetry]
13. WindEffectMixIn                           [on_telemetry]
14. MsfsXpNosewheelShimmyMixIn                [on_telemetry]
15. MsfsXpFlightControlsMixIn                 [on_telemetry, on_timeout]
16. MsfsXpFBWFlightControlsMixIn              [on_timeout]
17. AoAEffectsMixIn                           [on_telemetry]
18. AdvancedSpringMixIn                       [on_telemetry]
19. GForceEffectMixIn                         [on_telemetry]
20. DynamicSpringMixin
21. MotionEffectsMixIn                        [on_telemetry]
22. BuffetingEffectMixIn                      [on_telemetry]
23. MsfsXpSimConnectMixIn
24. AircraftEffectUtilsBase                   [on_telemetry, on_timeout, on_event]
25. AircraftParamsMixIn
26. object
```

## Running the Tests

```bash
# Run all MRO tests
pytest tests/test_msfs_aircraft_mro.py -v

# Run with output to see the MRO chain
pytest tests/test_msfs_aircraft_mro.py -v -s

# Run just the documentation test to see the MRO
pytest tests/test_msfs_aircraft_mro.py::TestMsfsAircraftMRO::test_mro_order_documentation -v -s
```

## Test Implementation

The test works by:
1. Creating an instance of `msfs_xp.Aircraft`
2. Wrapping each class's lifecycle method with a tracking wrapper
3. Calling the method on the instance
4. Verifying all classes in the MRO chain were called

This approach detects broken MRO chains caused by:
- Missing `super()` calls
- Early returns before `super()` calls
- Exception handling that prevents `super()` propagation
