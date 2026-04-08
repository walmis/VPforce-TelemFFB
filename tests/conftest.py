"""
Pytest configuration and shared fixtures for TelemFFB tests.
"""
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
