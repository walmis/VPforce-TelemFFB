"""Backend interface seam: FFBRhino/FFBEffectHandle conform to the
ffb_backend contract, capability flags describe the backends, and the
base class provides safe no-op defaults for VPforce-only operations."""
import pytest

from telemffb.hw.ffb_backend import (
    BaseEffectHandle, BaseFFBDevice, DeviceCapabilities, VPFORCE_CAPABILITIES,
)
from telemffb.hw.ffb_rhino import FFBEffectHandle, FFBRhino

pytestmark = [pytest.mark.unit]


class TestConformance:
    def test_rhino_is_a_backend_device(self):
        assert issubclass(FFBRhino, BaseFFBDevice)
        assert issubclass(FFBEffectHandle, BaseEffectHandle)

    def test_signals_come_from_the_base(self):
        for sig in ("buttonPressed", "buttonReleased", "deviceConnected"):
            assert hasattr(BaseFFBDevice, sig)
            assert hasattr(FFBRhino, sig)

    def test_rhino_reports_full_capabilities(self):
        rhino = FFBRhino.__new__(FFBRhino)  # no hardware in tests
        caps = rhino.caps
        assert caps is VPFORCE_CAPABILITIES
        assert caps.has_spring_adjuster and caps.has_detents
        assert caps.has_override_start and caps.has_axis_override
        assert caps.has_force_telemetry and caps.has_cp_telemetry
        assert caps.has_gains and caps.has_deadzone and caps.has_firmware_version


class TestBaseDefaults:
    """A minimal backend inherits capability-less, safe-no-op behavior."""

    class _Minimal(BaseFFBDevice):
        def create_effect(self, effect_type):
            return None

        def get_input(self):
            return None

    def test_default_caps_are_all_off(self):
        caps = self._Minimal().caps
        assert caps == DeviceCapabilities()
        assert not any([
            caps.has_spring_adjuster, caps.has_detents, caps.has_override_start,
            caps.has_axis_override, caps.has_force_telemetry, caps.has_cp_telemetry,
            caps.has_gains, caps.has_deadzone, caps.has_firmware_version,
        ])
        assert caps.effect_slots_hint is None

    def test_vpforce_only_surface_is_safe_noop(self):
        dev = self._Minimal()
        dev.set_deadzone(0)                 # no raise
        dev.set_gain(1, 50)                 # no raise
        dev.send_axis_override(1, 100)      # no raise
        dev.clear_axis_override()           # no raise
        dev.reset_effects()                 # no raise
        assert dev.get_gains() is None
        assert dev.get_firmware_version() is None
        assert dev.serial is None
        assert dev.supports_axis_override() is False

    def test_capabilities_are_immutable(self):
        with pytest.raises(Exception):
            VPFORCE_CAPABILITIES.has_gains = False
