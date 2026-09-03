"""Characterization coverage for TASK003 spring-center normalization.

Hardware-facing ``cpOffset`` assertions stay in Rhino device units across the
refactor.  Assertions on cached centers and private diagnostic telemetry start
in the legacy device-unit representation and are updated to normalized values
when the corresponding production phase lands.
"""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import telemffb.globals as G
import telemffb.sim.aircrafts_dcs as dcs_module
import telemffb.sim.aircrafts_il2 as il2_module
import telemffb.sim.base.AdvancedSpringMixIn as advanced_spring_module
import telemffb.sim.base.GForceEffectMixIn as gforce_module
import telemffb.sim.base.HelicopterEffectsMixIn as helicopter_effects_module
from telemffb.SettingsManager import GEffectModeEnum, SpringModeEnum
from telemffb.sim.BaseTelemetryData import BaseTelemetryData
from telemffb.sim.aircrafts_dcs import Aircraft as DcsAircraft
from telemffb.sim.aircrafts_il2 import Aircraft as Il2Aircraft
from telemffb.sim.base.AdvancedSpringMixIn import AdvancedSpringMixIn
from telemffb.sim.base.GForceEffectMixIn import GForceEffectMixIn
from telemffb.sim.base.HelicopterEffectsMixIn import HelicopterEffectsMixIn
from telemffb.sim.base.PedalSpringOverrideMixIn import PedalSpringOverrideMixIn
from tests.framework.base import BaseTelemetryEffectTestCase


class TestGForceOffsetCenterUnits(BaseTelemetryEffectTestCase):
    def _instance(self, gs=3.0):
        G.device_firmware_version = "v1.0.18"
        instance = self.create_test_instance(GForceEffectMixIn)
        instance.gforce_effect_mode = GEffectModeEnum.ADVANCED
        instance.gforce_effect_adv_curve = {
            "mode": "offset",
            "enable_neg": True,
        }
        telem = BaseTelemetryData({
            "src": "MSFS",
            "FFBType": "joystick",
            "G": gs,
            "AccBody": [0.0, 0.0, 0.0],
            "TAS": 50.0,
            "WeightOnWheels": [0],
        })
        instance._telem_data = telem
        instance._last_telem_data = telem.copy()
        return instance, telem

    def test_adv_spring_return_is_exact_device_offset_before_refactor(self, monkeypatch):
        instance, telem = self._instance(gs=3.0)
        monkeypatch.setattr(gforce_module.utils, "get_gain_from_gs", lambda *_args: (0.5, 0.25))

        result = instance.ac_update_gforce_effect(telem, adv_spr=True)

        assert result == -2048
        assert "gforce_spr" not in self.mock_effects.dict

    def test_standalone_offset_upload_and_negative_sign(self, monkeypatch):
        instance, telem = self._instance(gs=-2.0)
        monkeypatch.setattr(gforce_module.utils, "get_gain_from_gs", lambda *_args: (0.5, 0.25))

        instance.ac_update_gforce_effect(telem)

        effect = self.mock_effects["gforce_spr"]
        assert effect.started
        assert effect.get_offsets() == (0, 1024)
        assert effect._x_saturation == 4096
        assert effect._y_saturation == 4096


class TestAdvancedSpringCenterUnits(BaseTelemetryEffectTestCase):
    def _instance(self):
        G.device_firmware_version = "v1.0.18"
        instance = self.create_test_instance(AdvancedSpringMixIn)
        instance.spring_mode = SpringModeEnum.ADVANCED
        instance.adv_spr_gains = json.dumps({
            "gain_x": 100,
            "gain_y": 100,
            "curve_x": {"points": []},
            "curve_y": {"points": []},
            "units": "m/s",
        })
        instance.gforce_effect_mode = GEffectModeEnum.DISABLED
        instance.adv_spr_use_hardware_trim = True
        instance._telem_data = BaseTelemetryData({
            "src": "DCS",
            "FFBType": "joystick",
            "IAS": 50.0,
            "TAS": 50.0,
            "WeightOnWheels": [0],
            "ACCs": [0.0, 1.0, 0.0],
        })
        return instance

    def test_hat_trim_preserves_rate_and_device_output(self, monkeypatch):
        instance = self._instance()
        instance.override_spring_trim_rate = 200
        instance.override_spring_trim_down = 1
        instance.override_spring_trim_right = 2
        self.mock_device.get_input().press_button(1)
        self.mock_device.get_input().press_button(2)
        monkeypatch.setattr(
            advanced_spring_module.perftracker, "get_time_delta", lambda _key: 0.25)
        monkeypatch.setattr(
            advanced_spring_module.utils, "get_gain_from_speed", lambda *_args: {"x": 0.5, "y": 0.5})

        instance.ac_modify_game_spring()

        assert instance.override_spring_cp0_x == 50
        assert instance.override_spring_cp0_y == -50
        assert instance.telem_data._ovrd_spr_step == 50
        assert instance.telem_data._ovrd_spr_trim_pos[:2] == [50, -50]
        assert self.mock_effects["adv_spr"].get_offsets() == (50, -50)

    def test_trim_clamps_and_reset_uploads_zero(self, monkeypatch):
        instance = self._instance()
        instance.override_spring_cp0_x = 4080
        instance.override_spring_cp0_y = -4080
        instance.override_spring_trim_rate = 200
        instance.override_spring_trim_right = 2
        instance.override_spring_trim_down = 1
        self.mock_device.get_input().press_button(1)
        self.mock_device.get_input().press_button(2)
        monkeypatch.setattr(
            advanced_spring_module.perftracker, "get_time_delta", lambda _key: 0.25)
        monkeypatch.setattr(
            advanced_spring_module.utils, "get_gain_from_speed", lambda *_args: {"x": 0.5, "y": 0.5})

        instance.ac_modify_game_spring()
        assert (instance.override_spring_cp0_x, instance.override_spring_cp0_y) == (4096, -4096)
        assert self.mock_effects["adv_spr"].get_offsets() == (4096, -4096)

        self.mock_device.get_input().release_button(1)
        self.mock_device.get_input().release_button(2)
        instance.override_spring_trim_reset = 3
        self.mock_device.get_input().press_button(3)
        instance.ac_modify_game_spring()
        assert (instance.override_spring_cp0_x, instance.override_spring_cp0_y) == (0, 0)
        assert self.mock_effects["adv_spr"].get_offsets() == (0, 0)


class TestPedalForceTrimCenterUnits(BaseTelemetryEffectTestCase):
    def _instance(self):
        instance = self.create_test_instance(PedalSpringOverrideMixIn)
        instance._telem_data = BaseTelemetryData({"src": "DCS", "FFBType": "pedals"})
        instance.pedal_ft_release_button = 7
        instance.pedal_ft_reset_button = 8
        return instance

    def test_release_captures_center_and_device_output(self):
        instance = self._instance()
        self.mock_device.get_input().set_axis(x=0.25)
        self.mock_device.get_input().press_button(7)

        assert instance.ac_update_pedal_force_trim(instance.telem_data)
        assert instance.cpO_x == 1024
        assert instance.spring_x.cpOffset == 1024

    def test_cockpit_switch_off_uses_same_capture_path(self):
        instance = self._instance()
        self.mock_device.get_input().set_axis(x=-0.3)

        assert instance.ac_update_pedal_force_trim(instance.telem_data, ft_active=False)
        assert instance.cpO_x == round(-0.3 * 4096)
        assert instance.spring_x.cpOffset == round(-0.3 * 4096)

    def test_reset_uploads_each_intermediate_center(self):
        instance = self._instance()
        instance.cpO_x = 1024
        instance.pedal_trim_reset_complete = False
        calls = []

        def step(key, value, timeframe_ms, destination, **kwargs):
            calls.append((key, value, timeframe_ms, destination, kwargs))
            return 768

        instance.step_value_over_time = step
        assert instance.ac_update_pedal_force_trim(instance.telem_data)
        assert instance.cpO_x == 768
        assert instance.spring_x.cpOffset == 768
        assert not instance.pedal_trim_reset_complete
        assert calls == [("center_x", 1024, 1000, 0, {})]


class TestCollectiveForceTrimCenterUnits(BaseTelemetryEffectTestCase):
    def _instance(self, telem=None):
        instance = self.create_test_instance(HelicopterEffectsMixIn)
        instance.spring_mode = SpringModeEnum.FORCETRIM
        instance.collective_ft_ovd_release = 5
        instance.collective_ft_ovd_reset = 6
        instance.collective_ft_ovd_trim_down = 7
        instance.collective_ft_ovd_trim_up = 8
        instance.collective_ft_use_master_buttons = False
        instance._telem_data = telem or BaseTelemetryData({
            "src": "DCS",
            "FFBType": "collective",
            "WeightOnWheels": [0],
            "ForceTrimSW": True,
        })
        return instance

    def test_cockpit_switch_off_tracks_axis_without_starting(self):
        telem = BaseTelemetryData({
            "src": "DCS",
            "FFBType": "collective",
            "WeightOnWheels": [0],
            "ForceTrimSW": False,
        })
        instance = self._instance(telem)
        spring = self.mock_effects["collective_ft"].spring()
        self.mock_device.get_input().set_axis(y=0.25)

        instance.ac_collective_force_trim_override(telem, spring)

        assert instance.collective_ft_ovd_cp0_y == 1024
        assert instance.spring_y.cpOffset == 1024
        assert spring.get_offsets()[1] == 1024
        assert spring.start_count == 0

    def test_release_tracks_axis_and_starts_effect(self):
        instance = self._instance()
        spring = self.mock_effects["collective_ft"].spring()
        self.mock_device.get_input().set_axis(y=-0.3)
        self.mock_device.get_input().press_button(5)

        instance.ac_collective_force_trim_override(instance.telem_data, spring)

        expected = round(-0.3 * 4096)
        assert instance.collective_ft_ovd_cp0_y == expected
        assert instance.spring_y.cpOffset == expected
        assert spring.get_offsets()[1] == expected
        assert spring.started

    def test_reset_and_hat_trim_preserve_device_units(self, monkeypatch):
        instance = self._instance()
        spring = self.mock_effects["collective_ft"].spring()
        self.mock_device.get_input().press_button(6)
        monkeypatch.setattr(
            helicopter_effects_module.perftracker, "get_time_delta", lambda _key: 0.5)

        instance.ac_collective_force_trim_override(instance.telem_data, spring)
        assert instance.collective_ft_ovd_cp0_y == 4096
        assert instance.spring_y.cpOffset == 4096
        assert instance.telem_data._coll_ft_step == 100
        assert instance.telem_data._coll_ft_trim_pos == 4096

        self.mock_device.get_input().release_button(6)
        self.mock_device.get_input().press_button(8)
        instance.ac_collective_force_trim_override(instance.telem_data, spring)
        assert instance.collective_ft_ovd_cp0_y == 3996
        assert instance.spring_y.cpOffset == 3996
        assert instance.telem_data._coll_ft_trim_pos == 3996


class TestDcsSpringCenterUnits(BaseTelemetryEffectTestCase):
    def _instance(self, ffb_type="joystick"):
        instance = self.create_aircraft_instance(DcsAircraft, name="DCS Test")
        telem = BaseTelemetryData({
            "src": "DCS",
            "FFBType": ffb_type,
            "WeightOnWheels": [0],
            "ForceTrimSW": True,
        })
        self.set_telemetry(instance, telem)
        return instance, telem

    def test_collective_ground_init_and_follow_output(self):
        instance, telem = self._instance("collective")
        telem.WeightOnWheels = [1]
        self.set_telemetry(instance, telem)
        self.mock_device.get_input().set_axis(y=1.0)

        instance.dcs_override_collective_spring(telem)
        assert instance.cpO_y == 4096
        assert instance.spring_y.cpOffset == 4096
        assert instance.collective_init == 1

        self.mock_device.get_input().set_axis(y=0.35)
        instance.dcs_override_collective_spring(telem)
        assert instance.cpO_y == round(0.35 * 4096)
        assert instance.spring_y.cpOffset == round(0.35 * 4096)

    def test_pedal_trim_converts_only_at_condition_boundary(self, monkeypatch):
        instance, telem = self._instance("pedals")
        telem.controlsurfaces_rudder_right = -0.4
        self.mock_device.get_input().set_axis(x=0.1)
        instance.dcs_send_commands = MagicMock()

        lpf = SimpleNamespace(value=0.0, update=lambda value: value)
        monkeypatch.setattr(dcs_module, "LPFs", SimpleNamespace(get=lambda *_args: lpf))
        instance.dcs_update_pedal_trim(telem)

        assert instance.spring_x.cpOffset == round(0.3 * 4096)
        expected_offset = 0.4 - 0.1
        instance.dcs_send_commands.assert_called_once_with([
            f"LoSetCommand(2003, {0.1 - expected_offset})"])

    def test_custom_spring_release_and_hat_trim(self, monkeypatch):
        instance, _telem = self._instance("joystick")
        instance.spring_mode = SpringModeEnum.CUSTOM
        instance.override_spring_ft_enabled = True
        instance.override_spring_trim_release = 4
        self.mock_device.get_input().set_axis(x=0.25, y=-0.5)
        self.mock_device.get_input().press_button(4)
        monkeypatch.setattr(dcs_module.perftracker, "get_time_delta", lambda _key: 0.25)

        instance.dcs_override_spring()
        assert (instance.override_spring_cp0_x, instance.override_spring_cp0_y) == (1024, -2048)
        assert (instance.spring_x.cpOffset, instance.spring_y.cpOffset) == (1024, -2048)

        self.mock_device.get_input().release_button(4)
        instance.override_spring_trim_right = 5
        instance.override_spring_trim_down = 6
        self.mock_device.get_input().press_button(5)
        self.mock_device.get_input().press_button(6)
        instance.dcs_override_spring()
        assert (instance.override_spring_cp0_x, instance.override_spring_cp0_y) == (1074, -2098)
        assert (instance.spring_x.cpOffset, instance.spring_y.cpOffset) == (1074, -2098)
        assert instance.telem_data._ovrd_spr_step == 50
        assert instance.telem_data._ovrd_spr_trim_pos == [1074, -2098]


class TestIl2SpringCenterUnits(BaseTelemetryEffectTestCase):
    def test_custom_spring_hat_trim_and_clamp(self, monkeypatch):
        instance = self.create_aircraft_instance(Il2Aircraft, name="IL-2 Test")
        telem = BaseTelemetryData({"src": "IL2", "FFBType": "joystick"})
        self.set_telemetry(instance, telem)
        instance.spring_mode = SpringModeEnum.CUSTOM
        instance.override_spring_ft_enabled = True
        instance.override_spring_gain = 0.5
        instance.override_spring_trim_rate = 200
        instance.override_spring_trim_right = 5
        instance.override_spring_trim_down = 6
        instance.override_spring_cp0_x = 4080
        instance.override_spring_cp0_y = -4080
        self.mock_device.get_input().press_button(5)
        self.mock_device.get_input().press_button(6)
        monkeypatch.setattr(il2_module.perftracker, "get_time_delta", lambda _key: 0.25)

        instance.il2_override_spring()

        assert (instance.override_spring_cp0_x, instance.override_spring_cp0_y) == (4096, -4096)
        assert (instance.spring_x.cpOffset, instance.spring_y.cpOffset) == (4096, -4096)
        assert instance.telem_data._ovrd_spr_step == 50
        assert instance.telem_data._ovrd_spr_trim_pos == [4096, -4096]
        assert self.mock_effects["il2_spr_override"].started
