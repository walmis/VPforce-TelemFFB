"""DCS Autopilot Deadzone on a device without its own deadzone: while the
autopilot is engaged the stick position goes to DCS through the pitch and roll
axis commands, with a rescaling deadzone applied."""
from dataclasses import replace
from unittest.mock import MagicMock

import pytest

from telemffb.hw.ffb_backend import VPFORCE_CAPABILITIES
from telemffb.sim.BaseTelemetryData import BaseTelemetryData
from telemffb.sim.aircrafts_dcs import Aircraft as DcsAircraft
from tests.framework.base import BaseTelemetryEffectTestCase

pytestmark = pytest.mark.unit


def _sent_axes(call):
    """(pitch, roll) from one dcs_send_commands call."""
    pitch, roll = call.args[0]
    assert pitch.startswith("LoSetCommand(2001, ") and roll.startswith("LoSetCommand(2002, ")
    return float(pitch[19:-1]), float(roll[19:-1])


class TestDcsApAxisDeadzone(BaseTelemetryEffectTestCase):
    def _instance(self, has_deadzone=False):
        self.mock_device.caps = replace(VPFORCE_CAPABILITIES, has_deadzone=has_deadzone)
        instance = self.create_aircraft_instance(DcsAircraft, name="DCS Test")
        instance.ap_active_deadzone_enabled = True
        instance.ap_active_deadzone = 0.1
        instance.dcs_send_commands = MagicMock()
        instance.ac_set_deadzone_override = MagicMock()
        return instance

    def _frame(self, instance, ap, stick=(0.0, 0.0), detected=True):
        telem = BaseTelemetryData({"src": "DCS", "FFBType": "joystick", "APEnabled": int(ap)})
        if detected:
            telem["APDetection"] = 1
        self.mock_device.get_input().set_axis(x=stick[0], y=stick[1])
        self.set_telemetry(instance, telem)
        instance.dcs_send_commands.reset_mock()
        instance.dcs_update_ap_deadzone(telem)
        return instance.dcs_send_commands.call_args_list

    def test_inside_the_deadzone_dcs_gets_a_centered_stick(self):
        instance = self._instance()
        calls = self._frame(instance, True, (0.08, -0.05))
        assert _sent_axes(calls[0]) == (0.0, 0.0)

    def test_past_the_deadzone_the_input_starts_from_zero_and_reaches_full(self):
        instance = self._instance()
        pitch, roll = _sent_axes(self._frame(instance, True, (0.55, -0.2))[0])
        assert roll == pytest.approx(0.5, abs=1e-3)
        assert pitch == pytest.approx(-0.1111, abs=1e-3)
        pitch, roll = _sent_axes(self._frame(instance, True, (1.0, -1.0))[0])
        assert (pitch, roll) == (-1.0, 1.0)

    def test_disengaging_sends_the_stick_as_it_is_once_then_stops(self):
        instance = self._instance()
        self._frame(instance, True, (0.05, 0.3))
        calls = self._frame(instance, False, (0.05, 0.3))
        assert _sent_axes(calls[0]) == (pytest.approx(0.3), pytest.approx(0.05))
        assert self._frame(instance, False, (0.05, 0.3)) == []

    def test_nothing_is_sent_without_the_autopilot(self):
        instance = self._instance()
        assert self._frame(instance, False, (0.05, 0.3)) == []

    def test_a_device_with_its_own_deadzone_gets_it_set_instead(self):
        instance = self._instance(has_deadzone=True)
        assert self._frame(instance, True, (0.05, 0.3)) == []
        instance.ac_set_deadzone_override.assert_called_with(0.1)

    def test_the_trim_workaround_keeps_the_axis_commands(self):
        instance = self._instance()
        instance.trim_workaround = True
        assert self._frame(instance, True, (0.05, 0.3)) == []
