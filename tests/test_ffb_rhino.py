"""
Tests for telemffb.hw.ffb_rhino module.

These tests mock the HID and USB layers to test FFB device functionality
without requiring actual hardware.
"""
import ctypes
import pytest
from unittest.mock import MagicMock, Mock, patch, PropertyMock
from PyQt6.QtCore import QObject

# Mock the hid module before importing ffb_rhino
import sys
sys.modules['telemffb.hw.hid'] = MagicMock()

from telemffb.hw.ffb_rhino import (
    FFBRhino,
    FFBEffectHandle,
    HapticEffect,
    FFBReport_SetEffect,
    FFBReport_SetCondition,
    FFBReport_SetPeriodic,
    FFBReport_SetConstantForce,
    FFBReport_SetEnvelope,
    FFBReport_BlockFree,
    FFBReport_EffectOperation,
    FFBReport_Input,
    FFBReport_PIDStatus_Input,
    FFBReport_Get_Gains_Feature_Data,
    FFBReport_Set_Gain_Feature_Data_t,
    FFBReport_SetDeadzone,
    DeviceInfo,
    EFFECT_CONSTANT,
    EFFECT_SPRING,
    EFFECT_DAMPER,
    EFFECT_FRICTION,
    EFFECT_INERTIA,
    EFFECT_SINE,
    EFFECT_SQUARE,
    EFFECT_TRIANGLE,
    EFFECT_SAWTOOTHUP,
    EFFECT_SAWTOOTHDOWN,
    EFFECT_SPRING_ADJUSTER,
    OP_START,
    OP_STOP,
    OP_START_OVERRIDE,
    CONTROL_RESET,
    HIDDisconnectedError,
    FFB_GAIN_MASTER,
    FFB_GAIN_SPRING,
    FFB_GAIN_DAMPER,
    AXIS_ENABLE_X,
    AXIS_ENABLE_Y,
    AXIS_ENABLE_DIR,
    HID_REPORT_ID_INPUT,
    HID_REPORT_ID_PID_STATE_REPORT,
)


class MockHIDDevice:
    """Mock HID device for testing."""
    
    def __init__(self, path=b"mock_path"):
        self.path = path
        self.serial = "TEST123456"
        self.product = "Rhino FFB Joystick"
        self.manufacturer = "VPforce"
        self.nonblocking = False
        self._write_buffer = []
        self._read_buffer = []
        self._feature_reports = {}
        self._closed = False
    
    def write(self, data):
        """Mock write operation."""
        if self._closed:
            return -1
        self._write_buffer.append(bytes(data))
        return len(data)
    
    def read(self, size):
        """Mock read operation."""
        if self._closed or not self._read_buffer:
            return None
        return self._read_buffer.pop(0)
    
    def get_feature_report(self, report_id, size):
        """Mock get feature report."""
        return self._feature_reports.get(report_id, bytes(size))
    
    def send_feature_report(self, data):
        """Mock send feature report."""
        self._feature_reports[data[0]] = bytes(data)
    
    def close(self):
        """Mock close operation."""
        self._closed = True
    
    def add_input_report(self, report):
        """Add a report to the read buffer."""
        self._read_buffer.append(bytes(report))


@pytest.fixture
def mock_hid_device():
    """Provide a mock HID device."""
    return MockHIDDevice()


@pytest.fixture
def mock_device_info():
    """Provide mock device info."""
    return DeviceInfo(
        interface_number=0,
        manufacturer_string="VPforce",
        path="mock_path",
        product_id=0x2055,
        product_string="Rhino FFB Test Device",
        release_number=516,
        serial_number="TEST123456",
        usage=4,
        usage_page=1,
        vendor_id=0xFFFF
    )


@pytest.fixture
def mock_ffb_device(mock_hid_device, mock_device_info):
    """Provide a mock FFBRhino device."""
    with patch('telemffb.hw.hid.Device', return_value=mock_hid_device):
        with patch('telemffb.hw.ffb_rhino.FFBRhino.enumerate', return_value=[mock_device_info]):
            device = FFBRhino.__new__(FFBRhino)
            device.vid = 0xFFFF
            device.pid = 0x2055
            device.info = mock_device_info
            device.firmware_version = ""
            device._button_state = 0
            device._prev_hats = 0xFFFF
            device._in_reports = {}
            device._effect_handles = []
            device._dev = mock_hid_device
            QObject.__init__(device)
            return device


# ============================================================================
# Structure Tests
# ============================================================================

class TestFFBReportStructures:
    """Test FFB report structure definitions."""
    
    def test_set_effect_structure(self):
        """Test FFBReport_SetEffect structure."""
        effect = FFBReport_SetEffect(
            effectBlockIndex=1,
            effectType=EFFECT_CONSTANT,
            duration=1000,
            gain=2048
        )
        assert effect.effectBlockIndex == 1
        assert effect.effectType == EFFECT_CONSTANT
        assert effect.duration == 1000
        assert effect.gain == 2048
        # Structure size may vary based on alignment, just verify it's reasonable
        assert ctypes.sizeof(effect) >= 15
    
    def test_set_condition_structure(self):
        """Test FFBReport_SetCondition structure."""
        cond = FFBReport_SetCondition(
            effectBlockIndex=1,
            parameterBlockOffset=0,
            cpOffset=100,
            positiveCoefficient=2048,
            negativeCoefficient=2048,
            positiveSaturation=4096,
            negativeSaturation=4096,
            deadBand=0
        )
        assert cond.effectBlockIndex == 1
        assert cond.cpOffset == 100
        assert cond.positiveCoefficient == 2048
    
    def test_set_condition_set_offset(self):
        """Test FFBReport_SetCondition.set_offset method."""
        cond = FFBReport_SetCondition()
        
        # Test with integer
        cond.set_offset(1000)
        assert cond.cpOffset == 1000
        
        # Test with float
        cond.set_offset(0.5)
        assert cond.cpOffset == 2048
        
        # Test clamping
        cond.set_offset(10000)
        assert cond.cpOffset == 4096
        
        cond.set_offset(-10000)
        assert cond.cpOffset == -4096
    
    def test_set_condition_set_saturation(self):
        """Test FFBReport_SetCondition.set_saturation method."""
        cond = FFBReport_SetCondition()
        
        # Test with integer
        cond.set_saturation(2048)
        assert cond.positiveSaturation == 2048
        assert cond.negativeSaturation == 2048
        
        # Test with float
        cond.set_saturation(0.5)
        assert cond.positiveSaturation == 2048
        assert cond.negativeSaturation == 2048
    
    def test_set_condition_set_coefficient(self):
        """Test FFBReport_SetCondition.set_coefficient method."""
        cond = FFBReport_SetCondition()
        
        # Test with integer
        cond.set_coefficient(2048)
        assert cond.positiveCoefficient == 2048
        assert cond.negativeCoefficient == 2048
        
        # Test with float
        cond.set_coefficient(0.5)
        assert cond.positiveCoefficient == 2048
        assert cond.negativeCoefficient == 2048
    
    def test_set_periodic_structure(self):
        """Test FFBReport_SetPeriodic structure."""
        periodic = FFBReport_SetPeriodic(
            effectBlockIndex=1,
            magnitude=2048,
            offset=0,
            phase=45,
            period=100
        )
        assert periodic.effectBlockIndex == 1
        assert periodic.magnitude == 2048
        assert periodic.phase == 45
        assert periodic.period == 100
    
    def test_set_constant_force_structure(self):
        """Test FFBReport_SetConstantForce structure."""
        cf = FFBReport_SetConstantForce(
            effectBlockIndex=1,
            magnitude=2048
        )
        assert cf.effectBlockIndex == 1
        assert cf.magnitude == 2048
    
    def test_set_deadzone_structure(self):
        """Test FFBReport_SetDeadzone structure."""
        dz = FFBReport_SetDeadzone(deadzone=100)
        assert dz.deadzone == 100


class TestFFBReportInput:
    """Test FFBReport_Input structure and methods."""
    
    def test_input_report_structure(self):
        """Test FFBReport_Input structure."""
        report = FFBReport_Input()
        report.X = 2048
        report.Y = -2048
        report.Button0_31 = 0x01
        assert report.X == 2048
        assert report.Y == -2048
    
    def test_axis_xy(self):
        """Test axisXY method."""
        report = FFBReport_Input()
        report.X = 4096
        report.Y = -4096
        x, y = report.axisXY()
        assert x == 1.0
        assert y == -1.0
    
    def test_buttons_property(self):
        """Test buttons property."""
        report = FFBReport_Input()
        report.Button0_31 = 0x03  # Button 1 and 2
        report.Button32_47 = 0x01  # Button 33
        assert report.buttons & 0x03 == 0x03
        assert report.buttons & (1 << 32) != 0
    
    def test_is_button_pressed(self):
        """Test isButtonPressed method."""
        report = FFBReport_Input()
        report.Button0_31 = 0x01  # Button 1
        assert report.isButtonPressed(1) == True
        assert report.isButtonPressed(2) == False
    
    def test_get_pressed_buttons(self):
        """Test getPressedButtons method."""
        report = FFBReport_Input()
        report.Button0_31 = 0x05  # Button 1 and 3
        pressed = report.getPressedButtons()
        assert 1 in pressed
        assert 3 in pressed
        assert 2 not in pressed
    
    def test_hat_button_detection(self):
        """Test hat switch as button detection."""
        report = FFBReport_Input()
        # Hat 0 at position 2 (right)
        report.hats = 0x0002
        pressed = report.getPressedButtons()
        # Button number is 0x80 | (0 << 4) | 2 = 0x82
        assert 0x82 in pressed
    
    def test_cp_xy(self):
        """Test CP_XY method."""
        report = FFBReport_Input()
        report.CP_offsetX = 2048
        report.CP_offsetY = -2048
        cpX, cpY = report.CP_XY()
        assert cpX == 0.5
        assert cpY == -0.5
    
    def test_force_xy(self):
        """Test forceXY method."""
        report = FFBReport_Input()
        report.ForceX = 2048
        report.ForceY = -2048
        fx, fy = report.forceXY()
        assert fx == 0.5
        assert fy == -0.5
    
    def test_cp_scaled_axis_xy(self):
        """Test CP_scaled_axisXY method."""
        report = FFBReport_Input()
        report.X = 2048
        report.Y = 0
        report.CP_offsetX = 0
        report.CP_offsetY = 0
        
        scaled_x, scaled_y = report.CP_scaled_axisXY()
        assert scaled_x == 0.5
        assert scaled_y == 0.0


# ============================================================================
# FFBEffectHandle Tests
# ============================================================================

class TestFFBEffectHandle:
    """Test FFBEffectHandle class."""
    
    def test_effect_handle_creation(self, mock_ffb_device):
        """Test effect handle creation."""
        handle = FFBEffectHandle(mock_ffb_device, 1, EFFECT_CONSTANT)
        assert handle.effect_id == 1
        assert handle.type == EFFECT_CONSTANT
        assert handle.name == "Constant"
        assert not handle.started
    
    def test_effect_handle_bool(self, mock_ffb_device):
        """Test effect handle boolean evaluation."""
        handle = FFBEffectHandle(mock_ffb_device, 1, EFFECT_CONSTANT)
        assert bool(handle) == True
        
        handle.invalidate()
        assert bool(handle) == False
    
    def test_effect_handle_start(self, mock_ffb_device):
        """Test effect handle start."""
        handle = FFBEffectHandle(mock_ffb_device, 1, EFFECT_CONSTANT)
        handle.start()
        assert handle.started
        assert len(mock_ffb_device._dev._write_buffer) > 0
    
    def test_effect_handle_stop(self, mock_ffb_device):
        """Test effect handle stop."""
        handle = FFBEffectHandle(mock_ffb_device, 1, EFFECT_CONSTANT)
        handle.start()
        handle.stop()
        assert not handle.started
    
    def test_effect_handle_destroy(self, mock_ffb_device):
        """Test effect handle destroy."""
        handle = FFBEffectHandle(mock_ffb_device, 1, EFFECT_CONSTANT)
        handle.destroy()
        assert handle.effect_id is None
        assert handle.type == 0
    
    def test_set_constant_force(self, mock_ffb_device):
        """Test setConstantForce method."""
        handle = FFBEffectHandle(mock_ffb_device, 1, EFFECT_CONSTANT)
        handle.setConstantForce(0.5, 90)
        
        # Verify write was called
        assert len(mock_ffb_device._dev._write_buffer) > 0
    
    def test_set_constant_force_caching(self, mock_ffb_device):
        """Test that setConstantForce caches data."""
        handle = FFBEffectHandle(mock_ffb_device, 1, EFFECT_CONSTANT)
        handle.setConstantForce(0.5, 90)
        initial_writes = len(mock_ffb_device._dev._write_buffer)
        
        # Same data should not trigger write
        handle.setConstantForce(0.5, 90)
        assert len(mock_ffb_device._dev._write_buffer) == initial_writes
        
        # Different data should trigger write
        handle.setConstantForce(0.6, 90)
        assert len(mock_ffb_device._dev._write_buffer) > initial_writes
    
    def test_set_periodic(self, mock_ffb_device):
        """Test setPeriodic method."""
        handle = FFBEffectHandle(mock_ffb_device, 1, EFFECT_SINE)
        handle.setPeriodic(10, 0.5, 45)
        
        # Verify write was called
        assert len(mock_ffb_device._dev._write_buffer) > 0
    
    def test_set_condition(self, mock_ffb_device):
        """Test setCondition method."""
        handle = FFBEffectHandle(mock_ffb_device, 1, EFFECT_SPRING)
        cond = FFBReport_SetCondition(
            parameterBlockOffset=0,
            positiveCoefficient=2048,
            negativeCoefficient=2048
        )
        handle.setCondition(cond)
        
        # Verify write was called
        assert len(mock_ffb_device._dev._write_buffer) > 0


# ============================================================================
# FFBRhino Device Tests
# ============================================================================

class TestFFBRhinoDevice:
    """Test FFBRhino device class."""
    
    def test_device_properties(self, mock_ffb_device):
        """Test device properties."""
        assert mock_ffb_device.serial == "TEST123456"
        assert mock_ffb_device.product == "Rhino FFB Joystick"
        assert mock_ffb_device.manufacturer == "VPforce"
    
    def test_enumerate(self):
        """Test device enumeration."""
        import telemffb.hw.hid as hid
        
        mock_devices = [
            {
                'interface_number': 0,
                'manufacturer_string': 'VPforce',
                'path': b'test_path_1',
                'product_id': 0x2055,
                'product_string': 'Rhino FFB Joystick',
                'release_number': 516,
                'serial_number': 'TEST1',
                'usage': 4,
                'usage_page': 1,
                'vendor_id': 0xFFFF
            }
        ]
        
        # Mock the hid.enumerate function directly
        hid.enumerate = Mock(return_value=mock_devices)
        
        devices = FFBRhino.enumerate()
        assert len(devices) == 1
        assert devices[0].serial_number == 'TEST1'
    
    def test_get_gains(self, mock_ffb_device):
        """Test get_gains method."""
        # Setup mock feature report
        gains_data = FFBReport_Get_Gains_Feature_Data()
        gains_data.master_gain = 80
        gains_data.spring_gain = 90
        mock_ffb_device._dev._feature_reports[0x56] = bytes(gains_data)
        
        gains = mock_ffb_device.get_gains()
        assert isinstance(gains, FFBReport_Get_Gains_Feature_Data)
    
    def test_set_gain(self, mock_ffb_device):
        """Test set_gain method."""
        mock_ffb_device.set_gain(FFB_GAIN_SPRING, 85)
        
        # Verify feature report was sent
        assert 0x57 in mock_ffb_device._dev._feature_reports
    
    def test_set_deadzone(self, mock_ffb_device):
        """Test set_deadzone method."""
        mock_ffb_device.set_deadzone(100)
        
        # Verify write was called
        assert len(mock_ffb_device._dev._write_buffer) > 0
    
    def test_reset_effects(self, mock_ffb_device):
        """Test reset_effects method."""
        mock_ffb_device.reset_effects()
        
        # Verify write was called with control reset
        assert len(mock_ffb_device._dev._write_buffer) > 0
    
    def test_create_effect(self, mock_ffb_device):
        """Test create_effect method."""
        # Setup mock response for effect creation
        block_load_response = bytearray([6, 1, 1, 0, 0])  # Report ID 6, effect 1, success
        mock_ffb_device._dev._feature_reports[6] = bytes(block_load_response)
        
        effect = mock_ffb_device.create_effect(EFFECT_CONSTANT)
        assert effect is not None
        assert effect.effect_id == 1
        assert effect.type == EFFECT_CONSTANT
    
    def test_read_reports(self, mock_ffb_device):
        """Test read_reports method."""
        # Add input report to buffer
        input_report = FFBReport_Input()
        input_report.reportId = HID_REPORT_ID_INPUT
        input_report.X = 2048
        mock_ffb_device._dev.add_input_report(input_report)
        
        # Read reports
        mock_ffb_device.read_reports()
        
        # Verify report was processed
        assert HID_REPORT_ID_INPUT in mock_ffb_device._in_reports
    
    def test_get_input(self, mock_ffb_device):
        """Test get_input method."""
        # Add input report
        input_report = FFBReport_Input()
        input_report.reportId = HID_REPORT_ID_INPUT
        input_report.X = 2048
        mock_ffb_device._dev.add_input_report(input_report)
        mock_ffb_device.read_reports()
        
        # Get input
        report = mock_ffb_device.get_input()
        assert report is not None
        assert report.X == 2048
    
    def test_button_press_signal(self, mock_ffb_device):
        """Test button press signal emission."""
        button_pressed = []
        mock_ffb_device.buttonPressed.connect(lambda b: button_pressed.append(b))
        
        # Create input report with button pressed
        input_report = FFBReport_Input()
        input_report.reportId = HID_REPORT_ID_INPUT
        input_report.Button0_31 = 0x01
        mock_ffb_device._dev.add_input_report(input_report)
        
        # Process report
        mock_ffb_device.read_reports()
        
        # Verify signal was emitted
        assert 0 in button_pressed
    
    def test_button_release_signal(self, mock_ffb_device):
        """Test button release signal emission."""
        button_released = []
        mock_ffb_device.buttonReleased.connect(lambda b: button_released.append(b))
        
        # First press a button
        mock_ffb_device._button_state = 0x01
        
        # Create input report with button released
        input_report = FFBReport_Input()
        input_report.reportId = HID_REPORT_ID_INPUT
        input_report.Button0_31 = 0x00
        mock_ffb_device._dev.add_input_report(input_report)
        
        # Process report
        mock_ffb_device.read_reports()
        
        # Verify signal was emitted
        assert 0 in button_released

    def test_supports_axis_override_with_short_report(self, mock_ffb_device):
        """Short HID input report (old firmware) → axis override not supported."""
        full_size = ctypes.sizeof(FFBReport_Input)
        # Old firmware omits RawX(2) + RawY(2) + status(1) = 5 bytes
        short_data = bytes(full_size - 5)
        mock_ffb_device._in_reports[HID_REPORT_ID_INPUT] = short_data
        assert mock_ffb_device.supports_axis_override() is False

    def test_supports_axis_override_with_full_report(self, mock_ffb_device):
        """Full-size HID input report → axis override supported."""
        full_size = ctypes.sizeof(FFBReport_Input)
        full_data = bytes(full_size)
        mock_ffb_device._in_reports[HID_REPORT_ID_INPUT] = full_data
        assert mock_ffb_device.supports_axis_override() is True

    def test_supports_axis_override_no_report(self, mock_ffb_device):
        """No input report received yet → axis override not supported."""
        mock_ffb_device._in_reports.clear()
        assert mock_ffb_device.supports_axis_override() is False

    def test_get_report_pads_short_data(self, mock_ffb_device):
        """Short HID report is zero-padded → RawX/RawY/status default to 0."""
        full_size = ctypes.sizeof(FFBReport_Input)
        short_data = bytes(full_size - 5)
        mock_ffb_device._in_reports[HID_REPORT_ID_INPUT] = short_data

        report = mock_ffb_device.get_report(HID_REPORT_ID_INPUT)
        assert report is not None
        assert report.RawX == 0
        assert report.RawY == 0
        assert report.status == 0
        assert report.rawAxisXY() == (0.0, 0.0)
        assert report.axisOverrideActive() is False


# ============================================================================
# HapticEffect Tests
# ============================================================================

class TestHapticEffect:
    """Test HapticEffect high-level interface."""
    
    @pytest.fixture
    def haptic_device(self, mock_ffb_device):
        """Setup HapticEffect with mock device."""
        HapticEffect.device = mock_ffb_device
        
        # Mock create_effect to return a proper effect handle
        def mock_create_effect(effect_type):
            return FFBEffectHandle(mock_ffb_device, 1, effect_type)
        
        mock_ffb_device.create_effect = mock_create_effect
        yield mock_ffb_device
        HapticEffect.device = None
    
    def test_constant_effect_lazy_creation(self, haptic_device):
        """Test constant effect with lazy initialization."""
        effect = HapticEffect()
        
        # Create effect (lazy - not created yet)
        effect.constant(0.5, 90)
        assert effect._pending_create is not None
        assert effect._h_effect is None
        
        # Start should create the effect
        effect.start()
        assert effect._h_effect is not None
        assert effect.started
    
    def test_constant_effect_update(self, haptic_device):
        """Test constant effect parameter updates."""
        effect = HapticEffect()
        effect.constant(0.5, 90)
        effect.start()
        
        # Update parameters
        effect.constant(0.6, 180)
        assert effect._h_effect is not None
    
    def test_periodic_effect(self, haptic_device):
        """Test periodic effect creation."""
        effect = HapticEffect()
        effect.periodic(10, 0.5, 45, effect_type=EFFECT_SINE)
        effect.start()
        
        assert effect._h_effect is not None
        assert effect.effect_type == EFFECT_SINE
    
    def test_spring_effect(self, haptic_device):
        """Test spring effect creation."""
        effect = HapticEffect()
        effect.spring(2048, 2048)
        effect.start()
        
        assert effect._h_effect is not None
        assert effect.effect_type == EFFECT_SPRING
    
    def test_damper_effect(self, haptic_device):
        """Test damper effect creation."""
        effect = HapticEffect()
        effect.damper(2048, 2048)
        effect.start()
        
        assert effect._h_effect is not None
        assert effect.effect_type == EFFECT_DAMPER
    
    def test_friction_effect(self, haptic_device):
        """Test friction effect creation."""
        effect = HapticEffect()
        effect.friction(2048, 2048)
        effect.start()
        
        assert effect._h_effect is not None
        assert effect.effect_type == EFFECT_FRICTION
    
    def test_inertia_effect(self, haptic_device):
        """Test inertia effect creation."""
        effect = HapticEffect()
        effect.inertia(2048, 2048)
        effect.start()
        
        assert effect._h_effect is not None
        assert effect.effect_type == EFFECT_INERTIA
    
    def test_spring_adjuster_effect(self, haptic_device):
        """Test spring adjuster effect creation."""
        effect = HapticEffect()
        effect.spring_adjuster(4096, 4096)
        effect.start()
        
        assert effect._h_effect is not None
        assert effect.effect_type == EFFECT_SPRING_ADJUSTER
    
    def test_effect_stop(self, haptic_device):
        """Test effect stop."""
        effect = HapticEffect()
        effect.constant(0.5, 90)
        effect.start()
        assert effect.started
        
        effect.stop()
        assert not effect.started
    
    def test_effect_destroy(self, haptic_device):
        """Test effect destroy."""
        effect = HapticEffect()
        effect.constant(0.5, 90)
        effect.start()
        
        effect.destroy()
        assert effect._h_effect is None
    
    def test_effect_name_property(self, haptic_device):
        """Test effect name property."""
        effect = HapticEffect()
        effect.name = "TestEffect"
        assert effect.name == "TestEffect"
    
    def test_set_condition_lazy(self, haptic_device):
        """Test setCondition with lazy initialization."""
        effect = HapticEffect()
        effect.spring(2048, 2048)
        
        # Set condition before effect is created
        cond = FFBReport_SetCondition(
            parameterBlockOffset=0,
            positiveCoefficient=3000,
            negativeCoefficient=3000
        )
        effect.setCondition(cond)
        
        # Conditions should be pending
        assert len(effect._pending_conditions) > 0
        
        # Start should apply pending conditions
        effect.start()
        assert len(effect._pending_conditions) == 0
    
    def test_set_condition_immediate(self, haptic_device):
        """Test setCondition on already created effect."""
        effect = HapticEffect()
        effect.spring(2048, 2048)
        effect.start()
        
        # Set condition after effect is created
        cond = FFBReport_SetCondition(
            parameterBlockOffset=0,
            positiveCoefficient=3000,
            negativeCoefficient=3000
        )
        effect.setCondition(cond)
        
        # Should be applied immediately
        assert len(haptic_device._dev._write_buffer) > 0


# ============================================================================
# Integration Tests
# ============================================================================

class TestFFBRhinoIntegration:
    """Integration tests for FFB Rhino functionality."""
    
    @pytest.fixture
    def haptic_system(self, mock_ffb_device):
        """Setup complete haptic system."""
        HapticEffect.device = mock_ffb_device
        
        # Mock create_effect
        effect_counter = [0]
        def mock_create_effect(effect_type):
            effect_counter[0] += 1
            return FFBEffectHandle(mock_ffb_device, effect_counter[0], effect_type)
        
        mock_ffb_device.create_effect = mock_create_effect
        yield mock_ffb_device
        HapticEffect.device = None
    
    def test_multiple_effects(self, haptic_system):
        """Test creating and managing multiple effects."""
        spring = HapticEffect().spring(2048, 2048)
        damper = HapticEffect().damper(1024, 1024)
        constant = HapticEffect().constant(0.5, 90)
        
        spring.start()
        damper.start()
        constant.start()
        
        assert spring.started
        assert damper.started
        assert constant.started
        
        spring.stop()
        damper.stop()
        constant.stop()
        
        assert not spring.started
        assert not damper.started
        assert not constant.started
    
    def test_effect_lifecycle(self, haptic_system):
        """Test complete effect lifecycle."""
        effect = HapticEffect()
        effect.name = "TestEffect"
        
        # Create
        effect.constant(0.5, 90)
        assert effect._pending_create is not None
        
        # Start
        effect.start()
        assert effect._h_effect is not None
        assert effect.started
        
        # Update
        effect.constant(0.6, 180)
        
        # Stop
        effect.stop()
        assert not effect.started
        
        # Destroy
        effect.destroy()
        assert effect._h_effect is None


# ============================================================================
# DeviceInfo Tests
# ============================================================================

class TestDeviceInfo:
    """Test DeviceInfo dataclass."""
    
    def test_device_info_creation(self):
        """Test DeviceInfo creation."""
        info = DeviceInfo(
            interface_number=0,
            manufacturer_string="VPforce",
            path="test_path",
            product_id=0x2055,
            product_string="Rhino FFB Test Device",
            release_number=516,
            serial_number="TEST123",
            usage=4,
            usage_page=1,
            vendor_id=0xFFFF
        )
        assert info.manufacturer_string == "VPforce"
        assert info.serial_number == "TEST123"
    
    def test_device_info_ident(self):
        """Test DeviceInfo ident property."""
        info = DeviceInfo(
            interface_number=0,
            manufacturer_string="VPforce",
            path="test_path",
            product_id=0x2055,
            product_string="Rhino FFB My Custom Device",
            release_number=516,
            serial_number="TEST123",
            usage=4,
            usage_page=1,
            vendor_id=0xFFFF
        )
        assert info.ident == "My Custom Device"


# ============================================================================
# Connection State Tests
# ============================================================================

class TestFFBRhinoConnected:
    """`connected` must mirror the HID handle, not the stale report cache."""

    def test_connected_tracks_hid_handle(self, mock_ffb_device):
        assert mock_ffb_device.connected is True
        mock_ffb_device._dev = None  # what timerEvent does on a failed read
        assert mock_ffb_device.connected is False
        mock_ffb_device._dev = MockHIDDevice()  # what reconnect() restores
        assert mock_ffb_device.connected is True


# ============================================================================
# Disconnected-Primitive Tests (Task 1)
# ============================================================================
#
# A device whose HID handle has been lost (hot-unplug / startup failure) must
# degrade to a catchable HIDDisconnectedError from the low-level primitives,
# and effect-handle stop/destroy must become cheap no-ops so the 60-120 Hz hot
# path never sees an exception (the 13:55:41 thread-death bug).

class TestDisconnectedPrimitives:
    """Low-level primitives raise HIDDisconnectedError when the handle is gone."""

    def _disconnect(self, mock_ffb_device):
        """Simulate a hot-unplug: the HID handle is dropped."""
        mock_ffb_device._dev = None

    def test_write_raises_when_disconnected(self, mock_ffb_device):
        self._disconnect(mock_ffb_device)
        with pytest.raises(HIDDisconnectedError):
            mock_ffb_device.write(b"\x01\x02")

    def test_write_succeeds_when_connected(self, mock_ffb_device):
        mock_ffb_device.write(b"\x01\x02")
        assert len(mock_ffb_device._dev._write_buffer) > 0

    def test_get_gains_raises_when_disconnected(self, mock_ffb_device):
        self._disconnect(mock_ffb_device)
        with pytest.raises(HIDDisconnectedError):
            mock_ffb_device.get_gains()

    def test_set_gain_raises_when_disconnected(self, mock_ffb_device):
        self._disconnect(mock_ffb_device)
        with pytest.raises(HIDDisconnectedError):
            mock_ffb_device.set_gain(FFB_GAIN_SPRING, 85)

    def test_set_gain_range_still_validated_when_connected(self, mock_ffb_device):
        # The range contract is independent of device state.
        with pytest.raises(ValueError):
            mock_ffb_device.set_gain(FFB_GAIN_SPRING, 101)

    def test_set_deadzone_raises_when_disconnected(self, mock_ffb_device):
        self._disconnect(mock_ffb_device)
        with pytest.raises(HIDDisconnectedError):
            mock_ffb_device.set_deadzone(100)

    def test_send_axis_override_raises_when_disconnected(self, mock_ffb_device):
        self._disconnect(mock_ffb_device)
        with pytest.raises(HIDDisconnectedError):
            mock_ffb_device.send_axis_override(x_mode=1, x_value=12)

    def test_create_effect_raises_when_disconnected(self, mock_ffb_device):
        self._disconnect(mock_ffb_device)
        with pytest.raises(HIDDisconnectedError):
            mock_ffb_device.create_effect(EFFECT_CONSTANT)

    def test_reset_effects_raises_when_disconnected(self, mock_ffb_device):
        self._disconnect(mock_ffb_device)
        with pytest.raises(HIDDisconnectedError):
            mock_ffb_device.reset_effects()


class TestDisconnectedEffectHandle:
    """stop()/destroy() are in-memory no-ops when the HID handle is gone."""

    def test_stop_is_noop_when_disconnected(self, mock_ffb_device):
        handle = FFBEffectHandle(mock_ffb_device, 1, EFFECT_CONSTANT)
        handle.start()
        assert handle.started
        old_dev = mock_ffb_device._dev
        writes_after_start = len(old_dev._write_buffer)
        assert writes_after_start > 0

        mock_ffb_device._dev = None  # hot-unplug mid-flight

        handle.stop()  # must not raise
        assert not handle.started
        # Nothing was written to the (now dead) HID handle.
        assert len(old_dev._write_buffer) == writes_after_start

    def test_stop_no_write_recorded_when_disconnected(self, mock_ffb_device):
        handle = FFBEffectHandle(mock_ffb_device, 1, EFFECT_CONSTANT)
        handle.start()
        old_dev = mock_ffb_device._dev
        writes_after_start = len(old_dev._write_buffer)
        assert writes_after_start > 0

        mock_ffb_device._dev = None
        handle.stop()  # no-op: no new write to the dead handle
        assert mock_ffb_device._dev is None  # still disconnected
        assert len(old_dev._write_buffer) == writes_after_start
        assert not handle.started

    def test_destroy_is_noop_write_when_disconnected(self, mock_ffb_device):
        handle = FFBEffectHandle(mock_ffb_device, 1, EFFECT_CONSTANT)
        writes_before = len(mock_ffb_device._dev._write_buffer)

        mock_ffb_device._dev = None  # hot-unplug

        handle.destroy()  # must not raise, must not write
        assert handle.effect_id is None
        assert handle.type == 0
        assert not handle.started
        assert bool(handle) is False  # invalidated, replayable on recovery

    def test_handle_still_usable_after_recovery(self, mock_ffb_device):
        handle = FFBEffectHandle(mock_ffb_device, 1, EFFECT_CONSTANT)
        mock_ffb_device._dev = None
        handle.stop()  # no-op while dead
        # Restore the handle; a subsequent stop writes again.
        mock_ffb_device._dev = MockHIDDevice()
        writes_before = len(mock_ffb_device._dev._write_buffer)
        handle.start()
        handle.stop()
        assert len(mock_ffb_device._dev._write_buffer) > writes_before


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
