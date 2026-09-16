"""
Device-loss gate for the X-Plane axis override (MsfsXpSimConnectMixIn).

A TelemFFB instance whose FFB device is not live must never claim the
simulator's axis control: the plugin pins the virtual yoke/rudder to the
values we send, so a dead instance would feed zeros (or stale values) and
silence the user's real controller. The gate must release an active
override when the device is lost and reclaim it when the device returns.
"""
import telemffb.globals as G
from telemffb.hw.ffb_rhino import HapticEffect
from tests.framework.base import BaseTelemetryEffectTestCase
from tests.framework.utils import TelemetryDataBuilder
from telemffb.sim.msfs_xp.MsfsXpSimConnectMixIn import MsfsXpSimConnectMixIn

# annotation-only in globals; None = no firmware version (see test_blade_slap.py)
G.device_firmware_version = getattr(G, "device_firmware_version", None)


class RecordingSocket:
    """Stands in for the UDP socket; records every command string."""

    def __init__(self):
        self.sent = []

    def sendto(self, data, addr):
        self.sent.append(data.decode("utf-8"))


class TestXpAxisOverrideDeviceGate(BaseTelemetryEffectTestCase):

    def setup_method(self):
        super().setup_method()
        self._orig_device_type = G.device_type
        G.device_type = "joystick"  # not reset by the shared fixture

    def teardown_method(self):
        G.device_type = self._orig_device_type
        super().teardown_method()

    def _instance(self, ffb_type="joystick"):
        instance = self.create_test_instance(MsfsXpSimConnectMixIn)
        instance._socket = RecordingSocket()
        instance.telemffb_controls_axes = True
        telem = TelemetryDataBuilder().ffb_type(ffb_type).build()
        self.set_telemetry(instance, telem)
        return instance

    def test_device_never_connected_never_claims_override(self):
        HapticEffect.device = None  # open() failed at startup
        instance = self._instance()
        instance.toggle_xp_control()
        assert instance._socket.sent == []

    def test_device_feeding_claims_override_once(self):
        instance = self._instance()  # base fixture: MockFFBDevice, connected
        instance.toggle_xp_control()
        assert instance._socket.sent == ["OVERRIDE:joystick=true"]
        instance.toggle_xp_control()  # steady state: no resend
        assert instance._socket.sent == ["OVERRIDE:joystick=true"]

    def test_hot_unplug_releases_override(self):
        instance = self._instance()
        instance.toggle_xp_control()
        assert instance._socket.sent == ["OVERRIDE:joystick=true"]
        # _in_reports keeps the last stale report on a real device —
        # connected is the only reliable liveness signal.
        self.mock_device.set_connected(False)
        instance.toggle_xp_control()
        assert instance._socket.sent == [
            "OVERRIDE:joystick=true", "OVERRIDE:joystick=false",
        ]
        instance.toggle_xp_control()  # stays released, no flapping
        assert instance._socket.sent == [
            "OVERRIDE:joystick=true", "OVERRIDE:joystick=false",
        ]

    def test_reconnect_reclaims_override(self):
        instance = self._instance()
        instance.toggle_xp_control()
        self.mock_device.set_connected(False)
        instance.toggle_xp_control()
        self.mock_device.set_connected(True)
        instance.toggle_xp_control()
        assert instance._socket.sent == [
            "OVERRIDE:joystick=true",
            "OVERRIDE:joystick=false",
            "OVERRIDE:joystick=true",
        ]

    def test_fresh_connect_without_reports_yet_holds_override(self):
        instance = self._instance()
        self.mock_device._input_data = None  # connected, first report pending
        instance.toggle_xp_control()
        assert instance._socket.sent == []

    def test_controls_disabled_while_active_still_releases(self):
        instance = self._instance()
        instance.toggle_xp_control()
        instance.telemffb_controls_axes = False
        instance.toggle_xp_control()
        assert instance._socket.sent == [
            "OVERRIDE:joystick=true", "OVERRIDE:joystick=false",
        ]

    def test_pedals_device_never_connected_never_claims_override(self):
        HapticEffect.device = None
        instance = self._instance(ffb_type="pedals")
        instance.toggle_xp_control()
        assert instance._socket.sent == []

    def test_firmware_backend_warning_only_on_transition(self, caplog):
        import logging
        instance = self._instance()
        instance.use_firmware_axis_override = True
        self.mock_device.set_connected(False)  # not supported while dead
        with caplog.at_level(logging.WARNING):
            instance.toggle_xp_control()
            instance.toggle_xp_control()
            instance.toggle_xp_control()
        warnings = [r for r in caplog.records
                    if "Firmware axis override" in r.message]
        assert len(warnings) == 1
        assert instance._socket.sent == []
