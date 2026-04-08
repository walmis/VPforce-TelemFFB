import logging

import telemffb.utils as utils
from telemffb.SettingsManager import SpringModeEnum
from telemffb.hw.ffb_rhino import HapticEffect
from telemffb.sim.base.AdvancedSpringMixIn import AdvancedSpringMixIn
from telemffb.sim.BaseTelemetryData import BaseTelemetryData

perftracker = utils.PerformanceTracker()

class HelicopterEffectsMixIn(AdvancedSpringMixIn):
    """Mixin for helicopter-specific configuration and effects."""

    # user parameters
    rotor_blade_count: int = 2

    # Engine/rotor rumble
    engine_rotor_rumble_enabled: bool = False
    heli_engine_rumble_intensity: float = 0.12

    # ETL / Overspeed
    etl_effect_enable: bool = True
    etl_start_speed: float = 6.0
    etl_stop_speed: float = 22.0
    etl_effect_intensity: float = 0.2
    etl_shake_frequency: float = 14.0

    overspeed_effect_enable: bool = True
    overspeed_shake_start: float = 70.0
    overspeed_shake_intensity: float = 0.2
    overspeed_shake_frequency: float = 0.0

    # Vortex Ring State (VRS)
    vrs_effect_enable: bool = False
    vrs_effect_intensity: float = 0.0
    vrs_threshold_speed: float = 0.0
    vrs_vs_onset: float = 0
    vrs_vs_max: float = 0

    # Collective force trim override settings
    collective_ft_ovd_enabled: bool = False
    collective_ft_ovd_release: int = 0
    collective_ft_ovd_spring_gain: float = 0.5
    collective_ft_ovd_tr_damper: float = 0.05
    collective_ft_ovd_reset: int = 0
    collective_ft_ovd_trim_rate: int = 200
    collective_ft_init: bool = False
    collective_ft_ovd_trim_down = 0
    collective_ft_ovd_trim_up = 0
    collective_ft_ovd_cp0_y = 4096
    collective_ft_use_master_buttons: bool = False
    # end of user parameters
    

    ########################################
    ######                            ######
    ######     Helicopter Effects     ######
    ######                            ######
    ########################################

    def ac_calc_etl_effect(self, telem_data: BaseTelemetryData, blade_ct=None):
        #  rotor = 245
        mod = telem_data.N
        tas = telem_data.TAS or 0
        WoW = sum(telem_data.WeightOnWheels or [0, 0, 0])
        if mod == "UH-60L":
            # UH60 always shows positive value for tailwheel
            wow_list = telem_data.WeightOnWheels or [0, 0, 0]
            WoW = wow_list[0] + wow_list[2]

        if self._sim_is_xplane():
            rotor = telem_data.PropRPM or 0
            if isinstance(rotor, list):
                rotor = rotor[0]
        else:
            rotor = telem_data.RotorRPM or 0
            if isinstance(rotor, list):
                rotor = max(rotor)
        if WoW > 0:
            # logging.debug("On the Ground, moving forward. Probably on a Ship! - Dont play effect!")
            self.effects.dispose("etlX", "etlY", "overspeedX", "overspeedY")
            return
        if blade_ct is None:
            blade_ct = 2
            rotor = 250

        self.etl_shake_frequency = (rotor / 75) * blade_ct
        self.overspeed_shake_frequency = self.etl_shake_frequency * 0.75

        etl_mid = (self.etl_start_speed + self.etl_stop_speed) / 2.0

        if (
            (tas >= self.etl_start_speed and tas <= self.etl_stop_speed)
            and self.etl_effect_intensity
            and self.etl_effect_enable
        ):
            shake = self.etl_effect_intensity * utils.gaussian_scaling(
                tas, self.etl_start_speed, self.etl_stop_speed, peak_percentage=0.5, curve_width=0.7
            )
            shake = utils.clamp(shake, 0.0, 1.0)
            self.effects["etlY"].periodic(self.etl_shake_frequency, shake, 0).start()
            self.effects["etlX"].periodic(self.etl_shake_frequency + 4, shake, 90).start()
            logging.debug(f"Playing ETL shake (freq = {self.etl_shake_frequency}, intens= {shake})")
        else:
            self.effects.dispose("etlX", "etlY")

        if tas >= self.overspeed_shake_start and self.overspeed_effect_enable:
            shake = self.overspeed_shake_intensity * utils.non_linear_scaling(
                tas, self.overspeed_shake_start, self.overspeed_shake_start + 15, curvature=0.7
            )
            shake = utils.clamp(shake, 0.0, 1.0)
            self.effects["overspeedY"].periodic(self.overspeed_shake_frequency, shake, 0).start()
            self.effects["overspeedX"].periodic(self.overspeed_shake_frequency + 4, shake, 90).start()
            logging.debug(f"Overspeed shake (freq = {self.etl_shake_frequency}, intens= {shake}) ")
        else:
            self.effects.dispose("overspeedX", "overspeedY")

    def ac_update_vrs_effect(self, telem_data: BaseTelemetryData):
        vs = telem_data.VerticalSpeed or 0
        if self._sim_is_dcs():
            # spd = abs(telem_data.VlctVectors[0])
            tas = telem_data.TAS
            adj_tas = tas - abs(vs)
            spd = adj_tas
            telem_data._adj_TAS = adj_tas
        else:
            spd = abs(telem_data.TAS or 0)
        wow = max(telem_data.WeightOnWheels or [1])
        # print(f"tas:{tas}, vs:{vs}, wow:{wow}")
        if not self.vrs_effect_enable or wow or spd > self.vrs_threshold_speed or vs > 0:
            # print("I'm out")
            self.effects.dispose("vrs_buffet", "vrs_buffet2")
            return

        if abs(vs) >= self.vrs_vs_onset:
            vs_factor = utils.scale(abs(vs), (self.vrs_vs_onset, self.vrs_vs_max), (0.0, self.vrs_effect_intensity))
            if spd == 0:
                spd_factor = 1
            else:
                spd_factor = utils.scale(spd, (spd * 1.2, spd), (0, 1))

            intensity = utils.clamp(vs_factor * spd_factor, 0, 1)

            self.effects["vrs_buffet"].periodic(10, intensity, utils.RandomDirectionModulator).start()
            self.effects["vrs_buffet2"].periodic(12, intensity, utils.RandomDirectionModulator).start()
        else:
            self.effects.dispose("vrs_buffet", "vrs_buffet2")

    def ac_update_heli_engine_rumble(self, telem_data: BaseTelemetryData, blade_ct=None):
        if not self.engine_rotor_rumble_enabled or not self.heli_engine_rumble_intensity:
            self.effects.dispose("rotor_rpm0-1", "rotor_rpm1-1")
            return
        if self._sim_is_xplane():
            rrpm = telem_data.PropRPM or 0
            if isinstance(rrpm, list):
                rrpm = rrpm[0]
        else:
            rrpm = telem_data.RotorRPM or 0
            if isinstance(rrpm, list):
                rrpm = max(rrpm)
        mod = telem_data.N
        tas = telem_data.TAS or 0
        eng_rpm = telem_data.EngRPM or 0
        if isinstance(eng_rpm, list):
            eng_rpm = max(eng_rpm)

        # rotor = telem_data.get("RotorRPM")

        if blade_ct is None:
            blade_ct = 2
            rrpm = 250

        if rrpm < 5:
            self.effects.dispose("rotor_rpm0-1", "rotor_rpm1-1")
            return

        logging.debug(f"Engine Rumble: Blade_Ct={blade_ct}, RPM={rrpm}")
        frequency = float(rrpm) / 45 * blade_ct

        median_modulation = 2
        frequency2 = frequency + median_modulation
        if frequency > 0 and eng_rpm > 0:
            logging.debug(f"Current Heli Engine Rumble Intensity = {self.heli_engine_rumble_intensity}")
            self.effects["rotor_rpm0-1"].periodic(
                frequency, self.heli_engine_rumble_intensity * 0.5, 0
            ).start()  # vib on X axis
            self.effects["rotor_rpm1-1"].periodic(
                frequency2, self.heli_engine_rumble_intensity * 0.5, 90
            ).start()  # vib on Y axis
        else:
            self.effects.dispose("rotor_rpm0-1", "rotor_rpm1-1")

    def ac_collective_force_trim_override(self, telem_data: BaseTelemetryData, spring):
        """
        Generic effect enabling spring force and hardware trim for collective axis.
        """

        if not self.is_collective():
            return
        if not self.spring_mode_is(SpringModeEnum.FORCETRIM):
            # If feature disabled, ensure spring is stopped and abort
            self.effects["collective_ft"].stop()
            return

        dt = perftracker.get_time_delta("collective_ft_perf")
        self.telem_data._coll_ft_dt = dt

        wow = sum(telem_data.WeightOnWheels or [1])

        input_data = HapticEffect.device.get_input()
        _, y = input_data.axisXY()
        current_buttons = input_data.getPressedButtons()

        force_trim_active = telem_data.ForceTrimSW
        if force_trim_active is None:
            force_trim_active = True

        if not force_trim_active:
            # Force trim is enabled, but the 'ForceTrimSW' flag is false, just move
            self.spring_y.set_coefficient(self.collective_ft_ovd_tr_damper)
            self.collective_ft_ovd_cp0_y = round(y * 4096)
            self.spring_y.set_offset(self.collective_ft_ovd_cp0_y)
            spring.setCondition(self.spring_y)
            return

        # decide what to do depending on which button is pressed
        if self.check_button_press(self.collective_ft_ovd_release, self.collective_ft_use_master_buttons):
            # use spring force as dampening.  Configured damper value applied as spring gain.  cpO will follow stick
            # as it is moved while spring force is enabled.
            # return from method so default spring gains do not get applied at the end of the method
            self.spring_y.set_coefficient(self.collective_ft_ovd_tr_damper)

            self.collective_ft_ovd_cp0_y = round(y * 4096)
            self.spring_y.set_offset(self.collective_ft_ovd_cp0_y)
            spring.setCondition(self.spring_y)
            spring.start(override=True)
            return

        elif self.check_button_press(self.collective_ft_ovd_reset, self.collective_ft_use_master_buttons):
            # if trim reset button pressed, set offsets back to 0
            # print("TRIM RESET")
            self.collective_ft_ovd_cp0_y = 4096
            self.spring_y.set_offset(self.collective_ft_ovd_cp0_y)
            spring.setCondition(self.spring_y)

        # calculate step size based on configured rate and delta time
        trim_step_size = self.collective_ft_ovd_trim_rate * dt

        self.telem_data._coll_ft_step = trim_step_size

        if self.check_button_press(self.collective_ft_ovd_trim_down, self.collective_ft_use_master_buttons):
            # shift offset based on previously calculated step size.  Ensure value does not exceed limits
            # print("TRIM DOWN")
            self.collective_ft_ovd_cp0_y += trim_step_size
            self.collective_ft_ovd_cp0_y = utils.clamp(self.collective_ft_ovd_cp0_y, -4096, 4096)
            self.spring_y.set_offset(round(self.collective_ft_ovd_cp0_y))
        elif self.check_button_press(self.collective_ft_ovd_trim_up, self.collective_ft_use_master_buttons):
            # shift offset based on previously calculated step size.  Ensure value does not exceed limits
            # print("TRIM UP")
            self.collective_ft_ovd_cp0_y -= trim_step_size
            self.collective_ft_ovd_cp0_y = utils.clamp(self.collective_ft_ovd_cp0_y, -4096, 4096)
            self.spring_y.set_offset(round(self.collective_ft_ovd_cp0_y))

        self.telem_data._coll_ft_trim_pos = round(self.collective_ft_ovd_cp0_y)

        # If trim release is not pressed, set spring gain based on user setting and start spring override
        self.spring_y.set_coefficient(self.collective_ft_ovd_spring_gain)

        spring.setCondition(self.spring_y)
        # ensure spring is started with override = true
        spring.start(override=True)

    def on_telemetry(self, telem_data: BaseTelemetryData):
        super().on_telemetry(telem_data)
        if self.is_helicopter():
            self.ac_calc_etl_effect(telem_data, blade_ct=self.rotor_blade_count)
            self.ac_update_heli_engine_rumble(telem_data, blade_ct=self.rotor_blade_count)
            self.ac_update_vrs_effect(telem_data)
    
