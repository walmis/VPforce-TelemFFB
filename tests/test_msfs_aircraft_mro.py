"""Test that msfs_xp.Aircraft class properly calls super() in its MRO chain.

This test verifies that on_telemetry, on_event, and on_timeout methods
properly propagate through the entire Method Resolution Order (MRO) chain
by ensuring each mixin's implementation calls super().

CRITICAL FINDING:
================
The test has identified that PedalSpringOverrideMixIn.on_telemetry() contains
an early return without calling super().on_telemetry(), which breaks the MRO
chain and prevents 16+ mixin classes from executing their telemetry handlers.

This means when the device is NOT pedals, the following effects are never 
processed:
- HelicopterEffectsMixIn
- WeaponsEffectMixIn  
- DeadzoneMixIn
- HydraulicLossMixIn
- MsfsXpTrimwheelMixIn
- MfsfXpSteeringFrictionEffectMixIn
- DecelerationEffectMixIn
- EngineRumbleMixIn
- WindEffectMixIn
- MsfsXpNosewheelShimmyMixIn
- MsfsXpFlightControlsMixIn
- AoAEffectsMixIn
- AdvancedSpringMixIn
- GForceEffectMixIn
- MotionEffectsMixIn
- BuffetingEffectMixIn
- AircraftEffectUtilsBase

FIX: PedalSpringOverrideMixIn.on_telemetry() should call super() BEFORE the
early return check, not after.
"""
import unittest.mock as mock
from tests.framework.base import BaseTelemetryEffectTestCase
from tests.framework.utils import TelemetryDataBuilder


class TestMsfsAircraftMRO(BaseTelemetryEffectTestCase):
    """Test MRO chain for msfs_xp.Aircraft class lifecycle methods."""

    def test_on_telemetry_calls_all_mro_classes(self):
        """Verify on_telemetry propagates through entire MRO chain."""
        from telemffb.sim.msfs_xp.Aircraft import Aircraft
        
        # Create instance
        aircraft = Aircraft("test_aircraft")
        aircraft._test_sim_is_msfs = True
        
        # Build minimal telemetry data
        telem = TelemetryDataBuilder().ffb_type("joystick").build()
        telem["VelWorld"] = [0, 0, 0]
        telem["AmbWind"] = [0, 0, 0]
        telem["Heading"] = 0
        telem["Pitch"] = 0
        telem["Roll"] = 0
        
        # Get the MRO chain (excluding object)
        mro_classes = [cls for cls in Aircraft.__mro__ if cls.__name__ != 'object']
        
        # Track which classes have on_telemetry defined
        classes_with_method = []
        for cls in mro_classes:
            if 'on_telemetry' in cls.__dict__:
                classes_with_method.append(cls.__name__)
        
        print(f"\nMRO classes with on_telemetry: {classes_with_method}")
        
        # Use a shared dict to track calls across all wrappers
        call_tracking = {}
        
        # Store original methods before patching
        original_methods = {}
        for cls in mro_classes:
            if 'on_telemetry' in cls.__dict__:
                original_methods[cls] = cls.on_telemetry
        
        # Create wrapper that tracks calls but uses the unbound original
        def make_tracking_wrapper(cls_name, original_unbound):
            def wrapper(self, telem_data):
                call_tracking[cls_name] = True
                # Call the original unbound method directly
                return original_unbound(self, telem_data)
            return wrapper
        
        # Apply patches
        try:
            for cls in mro_classes:
                if 'on_telemetry' in cls.__dict__:
                    wrapper = make_tracking_wrapper(cls.__name__, original_methods[cls])
                    cls.on_telemetry = wrapper
            
            # Call on_telemetry
            aircraft.on_telemetry(telem)
            
            # Verify all classes with on_telemetry were called
            missing_calls = []
            for cls in mro_classes:
                if 'on_telemetry' in cls.__dict__:
                    if not call_tracking.get(cls.__name__):
                        missing_calls.append(cls.__name__)
            
            if missing_calls:
                called_classes = [name for name in call_tracking.keys()]
                print(f"\n✗ Called: {called_classes}")
                print(f"✗ Missing: {missing_calls}")
                assert False, (
                    f"The following classes were not called, likely due to early return without super() call:\n"
                    f"{', '.join(missing_calls)}\n"
                    f"Check that each class's on_telemetry() calls super().on_telemetry() before any early returns"
                )
            
            print(f"✓ All {len(call_tracking)} classes called on_telemetry correctly")
            
        finally:
            # Restore original methods
            for cls, original in original_methods.items():
                cls.on_telemetry = original

    def test_on_timeout_calls_all_mro_classes(self):
        """Verify on_timeout propagates through entire MRO chain."""
        from telemffb.sim.msfs_xp.Aircraft import Aircraft
        
        # Create instance
        aircraft = Aircraft("test_aircraft")
        
        # Get the MRO chain
        mro_classes = [cls for cls in Aircraft.__mro__ if cls.__name__ != 'object']
        
        # Track which classes have on_timeout defined
        classes_with_method = []
        for cls in mro_classes:
            if 'on_timeout' in cls.__dict__:
                classes_with_method.append(cls.__name__)
        
        print(f"\nMRO classes with on_timeout: {classes_with_method}")
        
        # Use a shared dict to track calls
        call_tracking = {}
        
        # Store original methods before patching
        original_methods = {}
        for cls in mro_classes:
            if 'on_timeout' in cls.__dict__:
                original_methods[cls] = cls.on_timeout
        
        # Create wrapper that tracks calls
        def make_tracking_wrapper(cls_name, original_unbound):
            def wrapper(self):
                call_tracking[cls_name] = True
                return original_unbound(self)
            return wrapper
        
        try:
            for cls in mro_classes:
                if 'on_timeout' in cls.__dict__:
                    wrapper = make_tracking_wrapper(cls.__name__, original_methods[cls])
                    cls.on_timeout = wrapper
            
            # Call on_timeout
            aircraft.on_timeout()
            
            # Verify all classes with on_timeout were called
            missing_calls = []
            for cls in mro_classes:
                if 'on_timeout' in cls.__dict__:
                    if not call_tracking.get(cls.__name__):
                        missing_calls.append(cls.__name__)
            
            if missing_calls:
                called_classes = [name for name in call_tracking.keys()]
                print(f"\n✗ Called: {called_classes}")
                print(f"✗ Missing: {missing_calls}")
                assert False, (
                    f"The following classes were not called, likely due to early return without super() call:\n"
                    f"{', '.join(missing_calls)}\n"
                    f"Check that each class's on_timeout() calls super().on_timeout() before any early returns"
                )
            
            print(f"✓ All {len(call_tracking)} classes called on_timeout correctly")
            
        finally:
            # Restore original methods
            for cls, original in original_methods.items():
                cls.on_timeout = original

    def test_on_event_calls_all_mro_classes(self):
        """Verify on_event propagates through entire MRO chain."""
        from telemffb.sim.msfs_xp.Aircraft import Aircraft
        
        # Create instance
        aircraft = Aircraft("test_aircraft")
        
        # Get the MRO chain
        mro_classes = [cls for cls in Aircraft.__mro__ if cls.__name__ != 'object']
        
        # Track which classes have on_event defined
        classes_with_method = []
        for cls in mro_classes:
            if 'on_event' in cls.__dict__:
                classes_with_method.append(cls.__name__)
        
        print(f"\nMRO classes with on_event: {classes_with_method}")
        
        # Use a shared dict to track calls
        call_tracking = {}
        
        # Store original methods before patching
        original_methods = {}
        for cls in mro_classes:
            if 'on_event' in cls.__dict__:
                original_methods[cls] = cls.on_event
        
        # Create wrapper that tracks calls
        def make_tracking_wrapper(cls_name, original_unbound):
            def wrapper(self, event, *args):
                call_tracking[cls_name] = True
                return original_unbound(self, event, *args)
            return wrapper
        
        try:
            for cls in mro_classes:
                if 'on_event' in cls.__dict__:
                    wrapper = make_tracking_wrapper(cls.__name__, original_methods[cls])
                    cls.on_event = wrapper
            
            # Call on_event with a test event
            aircraft.on_event("TEST_EVENT", "arg1", "arg2")
            
            # Verify all classes with on_event were called
            for cls in mro_classes:
                if 'on_event' in cls.__dict__:
                    assert call_tracking.get(cls.__name__), (
                        f"{cls.__name__}.on_event was not called - "
                        f"check if super().on_event() is missing"
                    )
            
            print(f"✓ All {len(call_tracking)} classes called on_event correctly")
            
        finally:
            # Restore original methods
            for cls, original in original_methods.items():
                cls.on_event = original

    def test_mro_order_documentation(self):
        """Document the MRO order for reference."""
        from telemffb.sim.msfs_xp.Aircraft import Aircraft
        
        print("\n" + "="*70)
        print("Complete MRO chain for msfs_xp.Aircraft:")
        print("="*70)
        
        for i, cls in enumerate(Aircraft.__mro__, 1):
            has_on_telemetry = 'on_telemetry' in cls.__dict__
            has_on_timeout = 'on_timeout' in cls.__dict__
            has_on_event = 'on_event' in cls.__dict__
            
            methods = []
            if has_on_telemetry:
                methods.append("on_telemetry")
            if has_on_timeout:
                methods.append("on_timeout")
            if has_on_event:
                methods.append("on_event")
            
            method_str = f" [{', '.join(methods)}]" if methods else ""
            print(f"{i:2}. {cls.__name__:40} {method_str}")
        
        print("="*70)


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v", "-s"])
