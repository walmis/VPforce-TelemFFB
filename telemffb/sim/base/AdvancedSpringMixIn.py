import telemffb.globals as G
from telemffb.sim.base.DynamicSpringMixin import DynamicSpringMixin
import telemffb.utils as utils
from telemffb.SettingsManager import SpringModeEnum
from telemffb.hw.ffb_rhino import FFBReport_SetCondition, HapticEffect
from telemffb.sim.aircraft_base import perftracker
from telemffb.sim.base.AircraftEffectUtilsBase import AircraftEffectUtilsBase


class AdvancedSpringMixIn(AircraftEffectUtilsBase, DynamicSpringMixin):
    """Mixin for the Advanced/Custom spring override (advanced spring trimming and adjuster)."""
    adv_spr_override_enabled: bool = False   # deprecated
    adv_spr_gains: str = 'none'
    adv_spr_use_hardware_trim: bool = False

    # override spring trim button bindings and settings
    override_spring_trim_down: int = 0
    override_spring_trim_left: int = 0
    override_spring_trim_up: int = 0
    override_spring_trim_right: int = 0
    override_spring_trim_rate: int = 200
    override_spring_cp0_x: float = 0.0
    override_spring_cp0_y: float = 0.0

    def __init__(self, *args, **kwargs):
        # Ensure cooperative init ordering
        super().__init__(*args, **kwargs)

        # per-instance state used by advanced spring override
        self.adv_spr_settings_dict: dict = {}

        # condition objects and adjuster used by the advanced spring override
        self.spring_adjuster_x = FFBReport_SetCondition(parameterBlockOffset=0)
        self.spring_adjuster_y = FFBReport_SetCondition(parameterBlockOffset=1)
        # the spring_adjuster effect object (wrapper) from the global effects dispenser
        self.spring_adjuster = self.effects['spring_adjuster'].spring_adjuster()

    def ac_modify_game_spring(self):
        if not self.spring_mode_is(SpringModeEnum.ADVANCED):
            self.spring_adjuster.stop()
            return
        # Verify the device firmware meets the minimum version required to execute this effect
        # Flag error and abort if not met
        supported = utils.check_min_firmware_version(G.device_firmware_version, "v1.0.18")
        if not supported:
            self.flag_error('The Advanced/Custom Spring Override requires firmware v1.0.18 or higher.\n'
                            f'The device is currently running version {G.device_firmware_version}\n'
                            f'Please update your device firmware!')
            return
        if self.adv_spr_gains == 'none':
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
        offset = self.ac_update_gforce_effect(self.telem_data, adv_spr=True)  # Returns g force spring offset if effect enabled and in offset mode
        self.g_y_offset = offset if offset is not None else 0
        self.telem_data['_ovrd_spr_trim_pos'] = [round(self.override_spring_cp0_x), round(self.override_spring_cp0_y), self.g_y_offset]
        self.spring_adjuster_y.set_offset(round(self.override_spring_cp0_y + self.g_y_offset))
        self.spring_adjuster_x.set_offset(round(self.override_spring_cp0_x))

        self.spring_adjuster.setCondition(self.spring_adjuster_y)
        self.spring_adjuster.setCondition(self.spring_adjuster_x)
        self.spring_adjuster.start()