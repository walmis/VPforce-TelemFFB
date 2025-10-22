import json
import pytest

from telemffb.sim.base.GForceEffectMixIn import GForceEffectMixIn
from telemffb.SettingsManager import GEffectModeEnum

from tests.framework.base import BaseTelemetryEffectTestCase
from tests.framework.utils import TelemetryDataBuilder
from telemffb.sim.base.AdvancedSpringMixIn import AdvancedSpringMixIn


class DummyG(GForceEffectMixIn):
    def __init__(self):
        # avoid calling parent init to prevent hardware/effect setup
        self._gforce_effect_mode = GEffectModeEnum.DISABLED

    def flag_error(self, message):
        self._last_flag = message


def test_set_gforce_mode_none_sets_disabled():
    d = DummyG()
    d.gforce_effect_mode = None
    assert d.gforce_effect_mode == GEffectModeEnum.DISABLED


def test_set_gforce_mode_with_enum():
    d = DummyG()
    d.gforce_effect_mode = GEffectModeEnum.ADVANCED
    assert d.gforce_effect_mode == GEffectModeEnum.ADVANCED


def test_set_gforce_mode_with_valid_string():
    d = DummyG()
    d.gforce_effect_mode = 'NEW'
    assert d.gforce_effect_mode == GEffectModeEnum.NEW


def test_set_gforce_mode_with_invalid_string_raises():
    d = DummyG()
    with pytest.raises(ValueError):
        d.gforce_effect_mode = 'INVALID'


def test_set_gforce_mode_with_invalid_type_raises():
    d = DummyG()
    with pytest.raises(ValueError):
        d.gforce_effect_mode = 123


class TestGForceEffectAllocation(BaseTelemetryEffectTestCase):
    """Tests that each G-force mode allocates the expected effect and that disabling deallocates."""

    def test_each_mode_creates_effect(self):
        # iterate through all non-disabled modes and verify an effect is created
        for mode in GEffectModeEnum:
            if mode == GEffectModeEnum.DISABLED:
                continue

            # fresh test instance
            inst = self.create_test_instance(GForceEffectMixIn)
            setattr(inst, '_test_sim_is_msfs', True)

            # ensure globals and firmware satisfy advanced checks
            import telemffb.globals as G
            G.device_firmware_version = "v1.0.18"

            # configure advanced curve when needed
            if mode == GEffectModeEnum.ADVANCED:
                adv = {
                    "gain_pos": 100,
                    "gain_neg": 100,
                    "curve_pos": {"points": [{"x": 0, "y": 0}, {"x": 5, "y": 100}], "smooth_curve_enabled": False},
                    "curve_neg": {"points": [{"x": 0, "y": 0}, {"x": 5, "y": 100}], "smooth_curve_enabled": False},
                    "mode": "constant",
                }
                inst.gforce_effect_adv_curve = json.dumps(adv)

            # ensure device input supports CP_XY used by NEW effect path
            setattr(self.mock_device.get_input(), 'CP_XY', lambda: (0, 0))

            # telemetry that will trigger the effect
            telem = (
                TelemetryDataBuilder()
                .set("src", "MSFS")
                .set("FFBType", "joystick")
                .set("G", 3.0)
                .set("AccBody", [0, 3.0, 0])
                .set("TAS", 10.0)
                .build()
            )

            inst.gforce_effect_mode = mode
            inst.on_telemetry(telem)

            # expected effect name
            expected = "new_gforce" if mode == GEffectModeEnum.NEW else "gforce"
            effect = self.mock_effects.get(expected, None)
            assert effect is not None, f"Mode {mode} did not create effect '{expected}'"
            assert effect.start_count > 0 or effect.started, f"Effect '{expected}' was not started for mode {mode}"


    def test_disabled_deallocates_all_effects(self):
        inst = self.create_test_instance(GForceEffectMixIn)
        setattr(inst, '_test_sim_is_msfs', True)

        import telemffb.globals as G
        G.device_firmware_version = "v1.0.18"

        # create a legacy effect first
        telem = (
            TelemetryDataBuilder()
            .set("src", "MSFS")
            .set("FFBType", "joystick")
            .set("G", 3.0)
            .set("AccBody", [0, 3.0, 0])
            .set("TAS", 10.0)
            .build()
        )

        setattr(self.mock_device.get_input(), 'CP_XY', lambda: (0, 0))

        inst.gforce_effect_mode = GEffectModeEnum.LEGACY
        inst.on_telemetry(telem)

        # effect should exist
        assert "gforce" in self.mock_effects.dict

        # now disable and update
        inst.gforce_effect_mode = None
        inst.on_telemetry(telem)

        # all related gforce effects should be removed
        for key in ("gforce", "new_gforce", "gforce_spr", "offset_adjuster"):
            assert key not in self.mock_effects.dict, f"Effect '{key}' was not disposed when disabled"

    def test_advanced_spring_uses_gforce_offset_mode(self):
        """When advanced spring is active and G effect mode is ADVANCED with mode 'offset',
        advanced spring should start and receive the G-offset value (via adv_spr integration)."""
        # create instance of AdvancedSpringMixIn
        inst = self.create_test_instance(AdvancedSpringMixIn)
        setattr(inst, '_test_sim_is_msfs', True)

        import telemffb.globals as G
        G.device_firmware_version = "v1.0.18"

        # provide adv_spr_gains (required by AdvancedSpringMixIn)
        adv_spr = {
            "gain_x": 100,
            "gain_y": 100,
            "curve_x": {"points": [{"x": 0, "y": 0}, {"x": 100, "y": 100}], "smooth_curve_enabled": False},
            "curve_y": {"points": [{"x": 0, "y": 0}, {"x": 100, "y": 100}], "smooth_curve_enabled": False},
            "units": "m/s"
        }
        inst.adv_spr_gains = json.dumps(adv_spr)

        # enable advanced spring mode so ac_modify_game_spring executes
        from telemffb.SettingsManager import SpringModeEnum
        inst.spring_mode = SpringModeEnum.ADVANCED

        # configure G-force advanced curve with 'offset' mode
        g_adv = {
            "gain_pos": 100,
            "gain_neg": 100,
            "curve_pos": {"points": [{"x": 0, "y": 0}, {"x": 5, "y": 100}], "smooth_curve_enabled": False},
            "curve_neg": {"points": [{"x": 0, "y": 0}, {"x": 5, "y": 100}], "smooth_curve_enabled": False},
            "mode": "offset",
        }
        inst.gforce_effect_adv_curve = json.dumps(g_adv)

        # provide telemetry that will produce positive G and trigger offset
        telem = (
            TelemetryDataBuilder()
            .set("src", "MSFS")
            .set("FFBType", "joystick")
            .set("G", 3.0)
            .set("AccBody", [0, 3.0, 0])
            .set("TAS", 10.0)
            .build()
        )

        # ensure device input CP_XY exists
        setattr(self.mock_device.get_input(), 'CP_XY', lambda: (0, 0))

        # set mode and call telemetry
        inst.gforce_effect_mode = GEffectModeEnum.ADVANCED
        inst.on_telemetry(telem)

        # Advanced spring adjuster should have been started via ac_modify_game_spring
        adv_spr_effect = getattr(inst, 'spring_adjuster', None)
        assert adv_spr_effect is not None, "Advanced spring adjuster effect not allocated on instance"

        # advanced spring should have recorded offset in telem_data
        assert '_ovrd_spr_trim_pos' in inst.telem_data, "Advanced spring did not populate override trim position"
        # The g offset is the third element in the list
        g_offset = inst.telem_data['_ovrd_spr_trim_pos'][2]
        assert isinstance(g_offset, int) or isinstance(g_offset, float), "G offset not present or invalid"
        # Ensure the 'gforce_spr' adjuster effect wasn't created by the dispenser in these modes
        assert 'gforce_spr' not in self.mock_effects.dict, f"'gforce_spr' was unexpectedly created for mode {inst.gforce_effect_mode}"