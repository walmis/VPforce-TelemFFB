import telemffb.globals as G
import telemffb.utils as utils
from telemffb.SettingsManager import GEffectModeEnum
from telemffb.hw.ffb_rhino import FFBReport_SetCondition, HapticEffect
from telemffb.sim.base.AircraftEffectUtilsBase import AircraftEffectUtilsBase


import logging


class GForceEffectMixIn(AircraftEffectUtilsBase):
    # user parameters
    # gforce_effect_master: bool = False
    # gforce_effect_enable: bool = False
    gforce_effect_invert_force = 0  # case where "180" degrees does not equal "away from pilot"
    gforce_effect_curvature = 2.2
    gforce_effect_max_intensity = 1.0
    gforce_min_gs = 1.5  # G's where the effect starts playing
    gforce_max_gs = 5.0  # G limit where the effect maxes out at strength defined in gforce_effect_max_intensity
    # gforce_effect_advanced_enabled = False
    gforce_effect_advanced_curve = {}
    gforce_current_factor: float = 0.0

    # new_gforce_effect_enable = False
    new_gforce_effect_center_deadzone = 0
    new_gforce_min_gs = 1.1  # G's where the effect starts playing
    new_gforce_max_gs = 5.0  # G limit where the effect maxes out at strength defined in gforce_effect_max_intensity
    new_gforce_effect_deflection_factor = 1.0
    new_gforce_enable_neg_gs = False
    new_gforce_min_gs_neg = 0.9
    new_gforce_max_gs_neg = -4
    new_gforce_effect_deflection_factor_neg = 1.0

    gforce_effect_adv_curve: str = "none"
    # end of user parameters

    def __init__(self):
        super().__init__()
        self._gforce_effect_mode = GEffectModeEnum.DISABLED

        self.__firmware_supported = None

        self.adv_g_settings_dict: dict = {}

        derivative_hz = 5  # derivative lpf filter -3db Hz
        self.__dGs = utils.Derivative(derivative_hz)

    @property
    def gforce_effect_mode(self) -> GEffectModeEnum:
        return self._gforce_effect_mode
    
    @gforce_effect_mode.setter
    def gforce_effect_mode(self, value):
        # Accept None, enum instances, and valid enum member names (strings).
        if value is None or value is False:
            self._gforce_effect_mode = GEffectModeEnum.DISABLED
            return

        # Enum instance -> accept
        if isinstance(value, GEffectModeEnum):
            self._gforce_effect_mode = value
            return

        # String -> map to enum if valid name
        if isinstance(value, str):
            if value in GEffectModeEnum.__members__:
                self._gforce_effect_mode = GEffectModeEnum[value]
                return
            else:
                raise ValueError(f"Invalid GEffectModeEnum mode string: {value}")
        
        # Any other type is invalid
        raise ValueError("Invalid type for gforce_effect_mode")
        
    def gforce_effect_mode_is(self, mode):
        return mode == self.gforce_effect_mode
    
    def on_telemetry(self, telem_data: dict):
        super().on_telemetry(telem_data)
        self.ac_update_gforce_effect(telem_data)

    def _ac_run_new_gforce_effect(self, telem_data):
        """Apply new G-force effects based on aircraft acceleration.

        Generates force feedback effects that vary with G-forces experienced by the aircraft.
        The effect strength is modulated by stick deflection and can handle both positive
        and negative G-forces if configured.

        Args:
            telem_data (dict): Telemetry data containing acceleration information
        """
        if (
            self._should_skip_joystick_effect()
            or not self.gforce_effect_mode_is(GEffectModeEnum.NEW)
            or self.gforce_effect_mode_is(GEffectModeEnum.DISABLED)
        ):
            self.effects.dispose("new_gforce")
            return
        if self._should_skip_airborne_effect(telem_data):
            self.effects.dispose("new_gforce")
            return
        if self._should_skip_no_airspeed_effect(telem_data):
            self.effects.dispose("new_gforce")
            return

        gmin = self.new_gforce_min_gs
        gmin_neg = self.new_gforce_min_gs_neg
        gmax = self.new_gforce_max_gs
        gmax_neg = self.new_gforce_max_gs_neg

        gs, y_gs, last_y_gs = self._get_gs_data(telem_data)
        if gs is None:
            return

        if self._is_telemetry_spike(y_gs, last_y_gs):
            self.effects.dispose("new_gforce")
            return

        logging.debug(f"GS={gs}, AVG_Z_GS={gs}")

        if gmin_neg < gs < gmin:
            self.effects["new_gforce"].stop()
            return

        input_data = HapticEffect.device.get_input()
        x, y = input_data.axisXY()
        _, spring_y_center = input_data.CP_XY()
        if spring_y_center is None:
            spring_y_center = 0
        derivative_k = 0.1  # derivative gain value, or damping ratio

        dGs = self.__dGs
        if gs > 1 and y > (spring_y_center + self.new_gforce_effect_center_deadzone):
            direction = 180
            g_factor = utils.scale_clamp(gs, (gmin, gmax), (0, 1))

            g_deriv = -dGs.update(g_factor) * derivative_k
            g_factor += g_deriv
            y_maxpoint = spring_y_center + (1 - spring_y_center) * self.new_gforce_effect_deflection_factor
            # utils.dbprint("green", f"y: {y}, syc:{spring_y_center}, y_max: {y_maxpoint}")
            deflection_factor = abs(utils.scale(y, (spring_y_center, y_maxpoint), (0, 1)))
        elif (
            gs < 1 and y < (spring_y_center - self.new_gforce_effect_center_deadzone) and self.new_gforce_enable_neg_gs
        ):
            direction = 0
            g_factor = utils.scale_clamp(gs, (gmin_neg, gmax_neg), (0, 1))

            g_deriv = -dGs.update(g_factor) * derivative_k
            g_factor += g_deriv
            y_maxpoint = abs(spring_y_center + (-1 - spring_y_center) * self.new_gforce_effect_deflection_factor_neg)
            # utils.dbprint("red", f"y_pos: {y}, spr_cent:{spring_y_center}, y_max: {y_maxpoint}")
            deflection_factor = abs(utils.scale(y, (spring_y_center, y_maxpoint), (0, -1)))
        else:
            self.effects["new_gforce"].stop()
            return

        telem_data["g_factor_raw"] = g_factor
        telem_data["g_deflection"] = deflection_factor
        # utils.dbprint("blue", f"g_deflection_factor: {deflection_factor}", "joystick")
        telem_data["g_y"] = y

        g_factor = g_factor * deflection_factor

        telem_data["g_factor"] = g_factor
        self.effects["new_gforce"].constant(g_factor, direction).start()
        logging.debug(f"G's = {gs} | gfactor = {g_factor}")

    def __dispose_all(self):
        self.effects.dispose("gforce", "new_gforce", "gforce_spr", "offset_adjuster")

    def ac_update_gforce_effect(self, telem_data, adv_spr=False):
        if not self.is_joystick():
            return
        
        if self.gforce_effect_mode_is(GEffectModeEnum.DISABLED):
            self.__dispose_all()
            return
        
        if self.gforce_effect_mode_is(GEffectModeEnum.NEW):
            # if "New" Gforce effect is enabled, call it instead and ensure the effect is disposed
            self.effects.dispose("gforce")
            self._ac_run_new_gforce_effect(telem_data)
            return
        else:
            self.effects.dispose("new_gforce")


        if self.gforce_effect_mode_is(GEffectModeEnum.ADVANCED):
            # Verify the device firmware meets the minimum version required to execute this portion of the effect
            # Flag error and abort if not met
            if self.__firmware_supported is None:
                self.__firmware_supported = utils.check_min_firmware_version(G.device_firmware_version, "v1.0.18")
            if not self.__firmware_supported:
                self.flag_error(
                    "The Advanced/Custom Curve G-Force effect requires firmware v1.0.18 or higher.\n"
                    f"The device is currently running version {G.device_firmware_version}\n"
                    f"Please update your device firmware!"
                )
                return

        if self._should_skip_airborne_effect(telem_data):
            self.__dispose_all()
            return
        if self._should_skip_no_airspeed_effect(telem_data):
            self.__dispose_all()
            return

        gs, y_gs, last_y_gs = self._get_gs_data(telem_data)
        if gs is None:
            return

        if self._is_telemetry_spike(y_gs, last_y_gs):
            self.__dispose_all()
            return

        logging.debug(f"GS={gs}, AVG_Z_GS={gs}")

        if self.gforce_effect_mode_is(GEffectModeEnum.LEGACY):
            gmin = self.gforce_min_gs
            gmax = self.gforce_max_gs
            direction = 180
            if gs < gmin:
                self.effects["gforce"].stop()
                return
            g_factor = round(utils.non_linear_scaling(gs, gmin, gmax, curvature=self.gforce_effect_curvature), 4)

            derivative_hz = 5  # derivative lpf filter -3db Hz
            derivative_k = 0.1  # derivative gain value, or damping ratio

            dGs = self.__dGs
            dGs.lpf.cutoff_freq_hz = derivative_hz

            g_deriv = -dGs.update(g_factor) * derivative_k

            g_factor += g_deriv

            g_factor = utils.clamp(g_factor, 0.0, 1.0)

            self.effects["gforce"].constant(g_factor, direction).start()

            logging.debug(f"G's = {gs} | gfactor = {g_factor}")

        elif self.gforce_effect_mode_is(GEffectModeEnum.ADVANCED):
            if self.gforce_effect_adv_curve == "none":
                self.flag_error("Please Configure the Advanced G-Force Effect Settings")
                self.effects.dispose("adv_gforce_constant")
                return
            if self.adv_g_settings_dict == {}:
                self.adv_g_settings_dict = utils.json.loads(self.gforce_effect_adv_curve)

            gains = utils.get_gain_from_gs(self.gforce_effect_adv_curve, abs(gs))
            mode = self.adv_g_settings_dict.get("mode", "constant")

            if gs >= 0:
                g_factor = gains.get("pos")
                direction = 180
            else:
                if self.adv_g_settings_dict.get("enable_neg"):
                    g_factor = -gains.get("neg")
                    direction = 0
                else:
                    self.effects.dispose("gforce", "gforce_spr")
                    return
           
            if mode == "constant":
                if not g_factor:
                    self.effects["gforce"].stop()
                    return
                
                g_factor = utils.clamp(g_factor, 0.0, 1.0)
                self.effects["gforce"].constant(g_factor, direction).start()

            elif mode == "offset":
                if not g_factor:
                    self.effects["gforce_spr"].stop()
                    return
                
                adjuster_cpOy = int(-g_factor * 4096)

                if adv_spr:
                    # If being called by advanced spring effect, don't apply adjuster offset here, return offset value and let the advanced spring adjuster effect do it
                    return adjuster_cpOy
                
                #_x = FFBReport_SetCondition(parameterBlockOffset=0)
                _y = FFBReport_SetCondition(parameterBlockOffset=1)
                _y.set_offset(adjuster_cpOy)
                _y.set_saturation(1)  #set relative adjustment mode
                _y.set_coefficient(4096)

                offset_adjuster : HapticEffect = self.effects["gforce_spr"].spring_adjuster(sat_x=1, sat_y=1)

                #_x.set_saturation(4096)
                #offset_adjuster.setCondition(_x)
                offset_adjuster.setCondition(_y)
                offset_adjuster.start()

        else:
            self.__dispose_all()
            return