import telemffb.utils as utils
from telemffb.SettingsManager import SpringModeEnum
from telemffb.hw.ffb_rhino import HapticEffect
from telemffb.sim.base.AircraftEffectUtilsBase import AircraftEffectUtilsBase
from telemffb.sim.base.DynamicSpringMixin import DynamicSpringMixin


class PedalSpringOverrideMixIn(AircraftEffectUtilsBase, DynamicSpringMixin):
    '''Pedal spring override and trimming mixin.'''

    pedal_spring_gain: float = 1.0
    pedal_trimming_enabled: bool = False
    pedal_dampening_gain = 0

    pedal_force_trim_enabled: bool = False
    pedal_ft_use_master_buttons: bool = False
    pedal_ft_release_button: int = 0
    pedal_ft_reset_button: int = 0
    pedal_ft_damper_enabled: bool = False
    pedal_ft_damper_force: float = 0.0
    pedal_trim_reset_complete: bool = False

    def ac_update_pedal_trim(self, telem_data):
        """Update the pedal trim effect based on telemetry data and user input.
        This method should be overridden in subclasses to implement specific pedal trim logic.
        """
        pass

    def ac_update_pedal_force_trim(self, telem_data, ft_active=True):
        if not self.is_pedals(): return

        input_data = HapticEffect.device.get_input()
        phys_x, phys_y = input_data.axisXY()

        force_trim_pressed = self.check_button_press(self.pedal_ft_release_button, self.pedal_ft_use_master_buttons)
        trim_reset_pressed = self.check_button_press(self.pedal_ft_reset_button, self.pedal_ft_use_master_buttons)

        if force_trim_pressed or not ft_active:
            if self.pedal_ft_damper_enabled:
                self.spring_x.set_coefficient(self.pedal_ft_damper_force)
            else:
                self.spring_x.set_coefficient(0)

            self.cpO_x = round(phys_x * 4096)
            self.spring_x.cpOffset = self.cpO_x
            return True

        if trim_reset_pressed or not self.pedal_trim_reset_complete:
            self.spring_x.set_coefficient(4096)
            self.cpO_x = self.step_value_over_time("center_x", self.cpO_x, 1000, 0)

            self.spring_x.cpOffset = self.cpO_x

            if self.cpO_x == 0:
                self.pedal_trim_reset_complete = True
            else:
                self.pedal_trim_reset_complete = False
            return True
        return False

    def ac_override_pedal_spring(self, telem_data):
        if not self.is_pedals(): return

        input_data = HapticEffect.device.get_input()
        phys_x, phys_y = input_data.axisXY()

        if self.spring_mode_is(SpringModeEnum.NONE):
            if self.effects['pedal_spring'].started:
                self.effects["pedal_spring"].stop()
            return

        if self.spring_mode_is(SpringModeEnum.NOSPRING):
            self.spring_x.set_coefficient(0)

        elif self.spring_mode_is(SpringModeEnum.STATIC):
            spring_coeff = utils.clamp(self.pedal_spring_gain, 0, 1.0)
            self.spring_x.set_coefficient(spring_coeff)
            if self.pedal_trimming_enabled and self._sim_is_dcs():
                self.ac_update_pedal_trim(telem_data)

        elif self.spring_mode_is(SpringModeEnum.FORCETRIM):
            if not self.ac_update_pedal_force_trim(telem_data):
                spring_coeff = utils.clamp(self.pedal_spring_gain, 0, 1.0)
                self.spring_x.set_coefficient(spring_coeff)

        elif self.spring_mode_is(SpringModeEnum.DYNAMIC) or self.spring_mode_is(SpringModeEnum.CUSTOM):
            tas = telem_data.get("TAS", 0)

            vs = self.aircraft_vs_speed
            vne = self.aircraft_vne_speed

            if vs > vne:
                self.flag_error(f"Dynamic pedal forces error: Vs speed ({vs}) is configured with a larger value than Vne ({vne}) - Invalid configuration")

            vs_coeff = utils.clamp(round(self.aircraft_vs_gain*4096), 0, 4096)
            vne_coeff = utils.clamp(round(self.aircraft_vne_gain*4096), 0, 4096)
            spr_coeff = utils.scale(tas, (vs, vne), (vs_coeff, vne_coeff))
            spr_coeff = round(spr_coeff * self.pedal_spring_gain)
            spr_coeff = utils.clamp(spr_coeff, 0, 4096)
            # print(f"coeff={spr_coeff}")
            self.spring_x.set_coefficient(spr_coeff)
            if self.pedal_trimming_enabled and self._sim_is_dcs():
                self.ac_update_pedal_trim(telem_data)
            # return
        spring = self.effects["pedal_spring"].spring()
        damper_coeff = round(utils.clamp((self.pedal_dampening_gain * 4096), 0, 4096))
        # self.damper = effects["pedal_damper"].damper(coef_x=damper_coeff).start()

        spring.setCondition(self.spring_x)
        spring.start(override=True)