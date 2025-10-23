import json
from typing import Optional, override

import telemffb.utils as utils
import telemffb.globals as G
from telemffb.sim.base.DynamicSpringMixin import DynamicSpringMixin
from telemffb.SettingsManager import GEffectModeEnum, SpringModeEnum
from telemffb.hw.ffb_rhino import FFBReport_SetCondition, HapticEffect
from telemffb.sim.base.GForceEffectMixIn import GForceEffectMixIn

perftracker = utils.PerformanceTracker()

class AdvancedSpringMixIn(GForceEffectMixIn, DynamicSpringMixin):
    """Mixin for the Advanced/Custom spring override (advanced spring trimming and adjuster)."""
    # user parameters
    _spring_mode : SpringModeEnum = SpringModeEnum.NONE

    adv_spr_override_enabled: bool = False   # deprecated
    adv_spr_use_hardware_trim: bool = False

    # override spring trim button bindings and settings
    override_spring_trim_down: int = 0
    override_spring_trim_left: int = 0
    override_spring_trim_up: int = 0
    override_spring_trim_right: int = 0
    override_spring_trim_rate: int = 200
    override_spring_cp0_x: float = 0.0
    override_spring_cp0_y: float = 0.0
    # end of user parameters

    @property
    def adv_spr_gains(self) -> Optional[dict]:
        return self._adv_spr_gains

    @adv_spr_gains.setter
    def adv_spr_gains(self, value: str):
        """adv_spr_gains gets assigned with json encoded string or 'none' via settings subsystem, save it as a dict internally"""
        if value == 'none':
            self._adv_spr_gains = None
            return
        self._adv_spr_gains = json.loads(value)

    @property
    def spring_mode(self) -> SpringModeEnum:
        return self._spring_mode

    @spring_mode.setter
    def spring_mode(self, value):
        # Accept None, enum instances, and valid enum member names (strings).
        if value is None:
            self._spring_mode = SpringModeEnum.NONE
            return

        # Enum instance -> accept
        if isinstance(value, SpringModeEnum):
            self._spring_mode = value
            return

        # String -> map to enum if valid name
        if isinstance(value, str):
            if value in SpringModeEnum.__members__:
                self._spring_mode = SpringModeEnum[value]
                return
            else:
                raise ValueError(f"Invalid spring mode string: {value}")

        # Any other type is invalid
        raise ValueError("Invalid type for spring_mode")

    def __init__(self, *args, **kwargs):
        # Ensure cooperative init ordering
        super().__init__(*args, **kwargs)
        self.__firmware_supported = None
        self._adv_spr_gains: Optional[dict] = None

        # per-instance state used by advanced spring override
        self.adv_spr_settings_dict: dict = {}
        # condition objects and adjuster used by the advanced spring override
        self.spring_adjuster_x = FFBReport_SetCondition(parameterBlockOffset=0)
        self.spring_adjuster_y = FFBReport_SetCondition(parameterBlockOffset=1)
        # the spring_adjuster effect object (wrapper) from the global effects dispenser
        self.spring_adjuster = HapticEffect().spring_adjuster()

    def spring_mode_is(self, mode : SpringModeEnum):
        return mode == self.spring_mode

    def ac_modify_game_spring(self):
        if not self.spring_mode_is(SpringModeEnum.ADVANCED):
            self.spring_adjuster.stop()
            return
        # Verify the device firmware meets the minimum version required to execute this effect
        # Flag error and abort if not met
        if self.__firmware_supported is None:
            self.__firmware_supported = utils.check_min_firmware_version(G.device_firmware_version, "v1.0.18")
        if not self.__firmware_supported:
            self.flag_error('The Advanced/Custom Spring Override requires firmware v1.0.18 or higher.\n'
                            f'The device is currently running version {G.device_firmware_version}\n'
                            f'Please update your device firmware!')
            return
        if not self.adv_spr_gains:
            self.flag_error('Please open and configure the advanced spring gain settings')
            return

        gains = utils.get_gain_from_speed(self.adv_spr_gains, self.telem_data.get('IAS', 0))

        self.spring_adjuster.name = 'adv_spr'
        self.spring_adjuster_y.set_coefficient(gains.get('y', 0))
        self.spring_adjuster_x.set_coefficient(gains.get('x', 0))

        if self.adv_spr_use_hardware_trim:
            dt = perftracker.get_time_delta('override_spring_perf')
            trim_step_size = self.override_spring_trim_rate * dt
            # trim_step_size = 200 * dt
            self.telem_data['_ovrd_spr_step'] = trim_step_size
            self.telem_data['_ovrd_spr_dt'] = dt
            # evaluate UP or DOWN and then LEFT or RIGHT trims.  Allows movement on both axes simultaneously but not
            # accidental confliction of trying to move both directions on a single axis due to bad hat bindings
            input_data = HapticEffect.device.get_input()
            x, y = input_data.axisXY()
            current_buttons = input_data.getPressedButtons()

            if self.override_spring_trim_down and self.override_spring_trim_down in current_buttons:
                self.override_spring_cp0_y -= trim_step_size
            elif self.override_spring_trim_up and self.override_spring_trim_up in current_buttons:
                self.override_spring_cp0_y += trim_step_size

            if self.override_spring_trim_left and self.override_spring_trim_left in current_buttons:
                self.override_spring_cp0_x -= trim_step_size
            elif self.override_spring_trim_right and self.override_spring_trim_right in current_buttons:
                self.override_spring_cp0_x += trim_step_size

            self.override_spring_cp0_x = round(utils.clamp(self.override_spring_cp0_x, -4096, 4096))
            self.override_spring_cp0_y = round(utils.clamp(self.override_spring_cp0_y, -4096, 4096))
        else:
            self.override_spring_cp0_x = 0
            self.override_spring_cp0_y = 0

        offset = super().ac_update_gforce_effect(self.telem_data, adv_spr=True)  # Returns g force spring offset if effect enabled and in offset mode
        g_y_offset = offset if offset is not None else 0
        self.telem_data['_ovrd_spr_trim_pos'] = [round(self.override_spring_cp0_x), round(self.override_spring_cp0_y), g_y_offset]
        self.spring_adjuster_y.set_offset(round(self.override_spring_cp0_y + g_y_offset))
        self.spring_adjuster_x.set_offset(round(self.override_spring_cp0_x))

        self.spring_adjuster.setCondition(self.spring_adjuster_y)
        self.spring_adjuster.setCondition(self.spring_adjuster_x)
        self.spring_adjuster.start()

    @override
    def ac_update_gforce_effect(self, telem_data: dict, adv_spr: bool = False) -> Optional[int]:
        """If advanced spring is enabled and in offset mode, handle gforce effect as part of advanced spring processing.
        else defer to GForceEffectMixIn implementation."""
        if not (self.gforce_effect_mode_is(GEffectModeEnum.ADVANCED) and self.g_effect_get_adv_mode() == "offset"):
            return super().ac_update_gforce_effect(telem_data, adv_spr=adv_spr)

    def on_telemetry(self, telem_data: dict):
        super().on_telemetry(telem_data)
        self.ac_modify_game_spring()
