"""
Pytest configuration and shared fixtures for TelemFFB tests.
"""
import importlib
import sys
from pathlib import Path

# Add parent directory to path for imports
parent_dir = Path(__file__).parent.parent
sys.path.insert(0, str(parent_dir))

import pytest
from tests.framework.base import MockHapticEffect, MockSimConnect, MockFFBDevice
from telemffb.sim.BaseTelemetryData import BaseTelemetryData
import telemffb.globals as G


@pytest.fixture(autouse=True)
def no_real_hardware(monkeypatch):
    """No test sees a device that happens to be plugged into this machine.

    The settings dialog enumerates hardware as part of being built -
    FFBRhino.enumerate() over HID, and the DirectInput listing through the
    bridge DLL - so any test that constructs one picks up whatever is
    attached.  A build machine has nothing attached, so a test written
    against a developer's rig passes there and fails in CI, or the other
    way round.  Both are stubbed empty here; a test that wants devices
    patches them itself, and its patch wins because it lands later.

    Stubbed at the hardware boundary rather than at FFBRhino.enumerate, so
    a test that fakes the HID layer still exercises the real enumeration
    code above it.

    Each import is attempted rather than assumed: hidapi and the DirectLink
    DLL are native libraries that a build machine need not have at all, and
    a fixture that insisted on importing them would turn "no hardware" into
    a collection error for every test in the suite.
    """
    class NoBridge:
        """DirectLink absent - which is what a build machine has.
        Tests that want devices inject their own bridge."""
        def enumerate(self):
            return []

    for module_name, attribute, stub in (
            ('telemffb.hw.hid', 'enumerate', lambda *a, **k: []),
            ('telemffb.hw.ffb_dinput', 'shared_bridge',
             lambda *a, **k: NoBridge()),
            # Where the DirectLink installer said it put its DLL.  Reads
            # HKCU on the machine running the tests, so a developer with
            # DirectLink installed would get different search paths from a
            # build agent without it.
            ('telemffb.hw.ffb_dinput', 'DIBridge.installed_location',
             staticmethod(lambda *a, **k: None)),
            # Game discovery finds DCS, BMS and the Steam libraries through
            # the registry.  A test that forgets to name a root would
            # otherwise find the developer's own install - and the install
            # paths write to whatever root they are handed.  Stubbed at the
            # one registry read they all share, so the discovery logic above
            # it is still the code under test.
            ('telemffb.tap_install', '_registry_values', lambda *a, **k: [])):
        try:
            module = importlib.import_module(module_name)
        except Exception:
            continue          # not importable here, so nothing to enumerate
        # attribute may name something inside a class ("DIBridge.foo"), so
        # walk to whatever owns the final name
        owner, _, name = attribute.rpartition('.')
        for step in filter(None, owner.split('.')):
            module = getattr(module, step)
        monkeypatch.setattr(module, name, stub, raising=False)


@pytest.fixture(autouse=True)
def setup_test_environment():
    """Setup test environment before each test."""
    # Initialize globals
    G.effects = None
    G.master_buttons = []
    yield
    # Cleanup after test
    G.effects = None
    G.master_buttons = []


@pytest.fixture
def mock_ffb_device():
    """Provide a mock FFB device for testing."""
    return MockFFBDevice()


@pytest.fixture
def mock_haptic_effect(mock_ffb_device):
    """Provide a mock haptic effect."""
    return MockHapticEffect(mock_ffb_device)


@pytest.fixture
def mock_simconnect():
    """Provide a mock SimConnect instance."""
    return MockSimConnect()


@pytest.fixture
def sample_telem_data():
    """Provide sample telemetry data for testing."""
    return BaseTelemetryData({
        "SimOnGround": 1,
        "WeightOnWheels": [1, 1, 1],
        "GroundSpeed": 10.0,
        "CenterSteerAngle": 0.0,
        "WaterRudderExt": 0.0,
        "SurfaceType": "Asphalt",
        "FFBType": "pedals",
        "AircraftType": "Unknown",
        "APMaster": 0,
        "ElevTrimPct": 0.0,
        "AileronTrimPct": 0.0,
        "RudderTrimPct": 0.0,
    })
