"""Tests that all aircraft classes across supported simulator modules can be imported
and instantiated without raising during initialization.

This uses the testing framework's BaseTelemetryEffectTestCase which sets up a
mock FFB device and mock effects dispenser so classes that access
`HapticEffect.device` or `G.effects` in their constructors won't touch real
hardware.
"""
import importlib
from typing import List, Tuple

from tests.framework.base import BaseTelemetryEffectTestCase


class TestAircraftInitialization(BaseTelemetryEffectTestCase):
    def test_import_and_initialize_all_aircraft_classes(self):
        """Import the aircraft modules and instantiate every class that is a
        subclass of AircraftBase. The test fails if any class raises during
        construction.
        """
        modules = [
            "telemffb.sim.aircrafts_dcs",
            "telemffb.sim.aircrafts_il2",
            "telemffb.sim.aircrafts_msfs_xp",
        ]

        from telemffb.sim.aircraft_base import AircraftBase

        errors: List[Tuple[str, str, Exception]] = []

        for mod_name in modules:
            mod = importlib.import_module(mod_name)

            # collect candidate classes
            classes = []
            for attr in dir(mod):
                obj = getattr(mod, attr)
                if isinstance(obj, type) and issubclass(obj, AircraftBase):
                    # skip the abstract base class itself
                    if obj is AircraftBase:
                        continue
                    classes.append((attr, obj))

            assert classes, f"No AircraftBase subclasses found in module {mod_name}"

            for cls_name, cls in classes:
                print(f"Testing {mod_name}.{cls_name}")
                try:
                    # Prefer positional name argument but allow keyword as well
                    try:
                        inst = cls(f"test_{cls_name}")
                    except TypeError:
                        inst = cls(name=f"test_{cls_name}")

                except Exception as exc:  # capture but don't immediately fail so we can report all
                    errors.append((mod_name, cls_name, exc))

        assert not errors, "Errors initializing aircraft classes:" + " ".join(
            [f"{m}.{c}: {e!r}" for m, c, e in errors]
        )
