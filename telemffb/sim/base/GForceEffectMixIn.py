import json
from typing import Literal, Optional, override
import telemffb.globals as G
import telemffb.utils as utils
from telemffb.SettingsManager import GEffectModeEnum
from telemffb.hw.ffb_rhino import FFBReport_SetCondition, HapticEffect
from telemffb.sim.base.AircraftEffectUtilsBase import AircraftEffectUtilsBase

import logging
from telemffb.sim.BaseTelemetryData import BaseTelemetryData

class GForceEffectProperties:
    """Separate class to hold GForce effect properties for mixin inheritance to reduce clutter."""

    _gforce_effect_mode = GEffectModeEnum.DISABLED
    _gforce_effect_adv_curve = {}
    # user parameters
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
            # Unknown name: see the spring_mode setter - fall back rather than
            # raise, so a stale config cannot block the aircraft from loading.
            logging.error(
                f"Unknown G-force effect mode '{value}' in this aircraft's "
                f"configuration - falling back to {GEffectModeEnum.DISABLED.name}. "
                "Choose a mode in the settings to replace it."
            )
            self._gforce_effect_mode = GEffectModeEnum.DISABLED
            return
        
        # Any other type is invalid
        raise ValueError("Invalid type for gforce_effect_mode")
    
    @property
    def gforce_effect_adv_curve(self) -> dict:
        return self._gforce_effect_adv_curve
    
    @gforce_effect_adv_curve.setter
    def gforce_effect_adv_curve(self, value):
        if isinstance(value, dict):
            self._gforce_effect_adv_curve = value
            return
        if not value or value == "none":
            self._gforce_effect_adv_curve = {}
        else:
            self._gforce_effect_adv_curve = json.loads(value)
    # end of user parameters

class GForceEffectMixIn(AircraftEffectUtilsBase, GForceEffectProperties):
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
    # end of user parameters

    # Absolute sanity ceiling on vertical G (gs) - a sustained reading beyond this is
    # physically implausible and indicates corrupt/crash telemetry rather than a real
    # maneuver. Independent of _is_telemetry_spike, which only catches the initial frame-
    # over-frame jump and stops firing once a bad value settles into a new "steady" state.
    gforce_absolute_gs_ceiling = 15.0

    def __init__(self):
        super().__init__()
        self.__firmware_supported = None

        derivative_hz = 5  # derivative lpf filter -3db Hz
        self.__dGs = utils.Derivative(derivative_hz)

    def __gforce_dispose_all(self):
        self.effects.dispose("gforce", "new_gforce", "gforce_spr", "offset_adjuster")

    def __check_firmware_support(self):
        if self.gforce_effect_mode_is(GEffectModeEnum.ADVANCED):
            caps = getattr(HapticEffect.device, 'caps', None)
            if caps is not None and not caps.has_spring_adjuster:
                # The Advanced curve itself is device-neutral: 'constant'
                # output renders via a plain constant force and works on any
                # backend.  Only the 'offset' output style needs the VPforce
                # spring adjuster.
                if self.g_effect_get_adv_mode() == "offset":
                    self.flag_error(
                        "The Advanced G-Force effect's 'offset' output mode is not supported on this device.\n"
                        "It requires the spring adjuster feature of VPforce hardware.\n"
                        "Switch the Advanced G-Force output mode to 'constant'."
                    )
                    return False
                # no firmware on generic devices: skip the version check
                return True
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
                return False
        return True

    def gforce_effect_mode_is(self, mode):
        return mode == self.gforce_effect_mode
    
    @override
    def on_telemetry(self, telem_data: BaseTelemetryData):
        super().on_telemetry(telem_data)
        self.ac_update_gforce_effect(telem_data)

    def _get_gs_data(self, telem_data: BaseTelemetryData) -> tuple:
        """Get G-force data based on simulator type.

        Telemetry:
            Read (MSFS/XPLANE):
                G       - float (g); vertical G load; 1.0 = 1 g level flight,
                          negative in inverted flight
                AccBody - List[float] ([x, y, z] in g); body-frame acceleration;
                          index [2] (longitudinal/lateral) used as y_gs for
                          cross-coupling; index [2] sign convention: + = forward
            Read (DCS/IL2/BMS):
                ACCs    - List[float] ([x, y, z] in g); index [1] = normal G (gs),
                          index [0] = lateral/longitudinal G (y_gs)
        """
        if self._sim_is("DCS") or self._sim_is("IL2") or self._sim_is('BMS'):
            accs = telem_data.ACCs
            if not accs:
                return None, None, None, None
            gs = accs[1]
            y_gs = accs[0]
            last_accs = self._last_telem_data.ACCs or [0, 0, 0]
            last_gs = last_accs[1]
            last_y_gs = last_accs[0]
        elif self._sim_is("MSFS") or self._sim_is('XPLANE'):
            gs = telem_data.G
            acc_body = telem_data.AccBody
            if not acc_body:
                return None, None, None, None
            y_gs = acc_body[2]
            last_acc_body = self._last_telem_data.AccBody or [0, 0, 0]
            last_gs = self._last_telem_data.G or 0
            last_y_gs = last_acc_body[2]
        else:
            return None, None, None, None
        return gs, y_gs, last_gs, last_y_gs

    def _ac_run_new_gforce_effect(self, telem_data: BaseTelemetryData):
        """Apply new G-force effects based on aircraft acceleration.

        Generates force feedback effects that vary with G-forces experienced by the aircraft.
        The effect strength is modulated by stick deflection and can handle both positive
        and negative G-forces if configured.

        Args:
            telem_data: Telemetry data containing acceleration information

        Telemetry:
            Read (via _get_gs_data, MSFS/XPLANE):
                G       - float (g); vertical G load (see _get_gs_data)
                AccBody - List[float] ([x, y, z] in g); index [2] as lateral G
            Written: g_factor_raw  (float, 0.0–1.0; raw G factor before deflection scaling)
                     g_deflection  (float, 0.0–1.0; stick deflection factor)
                     g_y           (float, -1.0–1.0; raw stick Y position)
                     g_factor      (float, 0.0–1.0; final G factor applied to effect)
        """
        if (
            self._should_skip_joystick_effect()
            or not self.gforce_effect_mode_is(GEffectModeEnum.NEW)
            or self.gforce_effect_mode_is(GEffectModeEnum.DISABLED)
            or self._should_skip_airborne_effect(telem_data)
            or self._should_skip_no_airspeed_effect(telem_data)
        ):
            self.effects.dispose("new_gforce")
            return

        gmin = self.new_gforce_min_gs
        gmin_neg = self.new_gforce_min_gs_neg
        gmax = self.new_gforce_max_gs
        gmax_neg = self.new_gforce_max_gs_neg

        gs, y_gs, last_gs, last_y_gs = self._get_gs_data(telem_data)
        if gs is None:
            return

        if (self._is_telemetry_spike(gs, last_gs)
                or self._is_telemetry_spike(y_gs, last_y_gs)
                or abs(gs) > self.gforce_absolute_gs_ceiling):
            logging.warning(f"[new_gforce] Rejected telemetry spike/ceiling: gs={gs} last_gs={last_gs} "
                             f"y_gs={y_gs} last_y_gs={last_y_gs} ceiling={self.gforce_absolute_gs_ceiling}")
            self.effects.dispose("new_gforce")
            return

        logging.debug(f"GS={gs}, AVG_Z_GS={gs}")

        if gmin_neg < gs < gmin:
            self.effects["new_gforce"].stop()
            return

        input_data = HapticEffect.get_device_input()
        x, y = self._get_device_axes()
        if input_data is not None:
            _, spring_y_center = input_data.CP_XY()
        else:
            spring_y_center = 0
        if spring_y_center is None:
            spring_y_center = 0
        derivative_k = 0.1  # derivative gain value, or damping ratio

        dGs = self.__dGs
        if gs > 1 and y >= (spring_y_center + self.new_gforce_effect_center_deadzone):
            direction = 180
            g_factor = utils.scale_clamp(gs, (gmin, gmax), (0, 1))

            g_deriv = -dGs.update(g_factor) * derivative_k
            g_factor += g_deriv
            y_maxpoint = spring_y_center + (1 - spring_y_center) * self.new_gforce_effect_deflection_factor
            # utils.dbprint("green", f"y: {y}, syc:{spring_y_center}, y_max: {y_maxpoint}")
            deflection_factor = abs(utils.scale(y, (spring_y_center, y_maxpoint), (0, 1)))
        elif (
            gs < 1 and y <= (spring_y_center - self.new_gforce_effect_center_deadzone) and self.new_gforce_enable_neg_gs
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

        telem_data.g_factor_raw = g_factor
        telem_data.g_deflection = deflection_factor
        # utils.dbprint("blue", f"g_deflection_factor: {deflection_factor}", "joystick")
        telem_data.g_y = y

        g_factor = g_factor * deflection_factor

        telem_data.g_factor = g_factor
        self.effects["new_gforce"].constant(g_factor, direction).start()
        logging.debug(f"G's = {gs} | gfactor = {g_factor}")

    def g_effect_get_adv_mode(self) -> Literal["constant", "offset"]:
        return self.gforce_effect_adv_curve.get("mode", "constant") if self.gforce_effect_adv_curve else "constant"
           
    def ac_update_gforce_effect(self, telem_data: BaseTelemetryData, adv_spr=False):
        """Dispatch to the configured G-force effect mode (DISABLED/LEGACY/ADVANCED/NEW).

        Telemetry:
            Read (via _get_gs_data, MSFS/XPLANE):
                G       - float (g); vertical G load
                AccBody - List[float] ([x, y, z] in g); index [2] as lateral G
            Written (NEW mode, via _ac_run_new_gforce_effect):
                g_factor_raw (float, 0.0–1.0), g_deflection (float, 0.0–1.0),
                g_y (float, -1.0–1.0), g_factor (float, 0.0–1.0)
        """
        if not self.is_joystick():
            return
        
        if self.gforce_effect_mode_is(GEffectModeEnum.DISABLED):
            self.__gforce_dispose_all()
            return
        
        if self.gforce_effect_mode_is(GEffectModeEnum.NEW):
            # if "New" Gforce effect is enabled, call it instead and ensure the effect is disposed
            self.effects.dispose("gforce")
            self._ac_run_new_gforce_effect(telem_data)
            return
        else:
            self.effects.dispose("new_gforce")

        if not self.__check_firmware_support():
            return

        if self._should_skip_airborne_effect(telem_data):
            self.__gforce_dispose_all()
            return
        if self._should_skip_no_airspeed_effect(telem_data):
            self.__gforce_dispose_all()
            return

        gs, y_gs, last_gs, last_y_gs = self._get_gs_data(telem_data)
        if gs is None:
            return

        if (self._is_telemetry_spike(gs, last_gs)
                or self._is_telemetry_spike(y_gs, last_y_gs)
                or abs(gs) > self.gforce_absolute_gs_ceiling):
            logging.warning(f"[gforce] Rejected telemetry spike/ceiling: gs={gs} last_gs={last_gs} "
                             f"y_gs={y_gs} last_y_gs={last_y_gs} ceiling={self.gforce_absolute_gs_ceiling}")
            self.__gforce_dispose_all()
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

            gain_pos, gain_neg = utils.get_gain_from_gs(self.gforce_effect_adv_curve, abs(gs))
            mode = self.g_effect_get_adv_mode()

            if gs >= 0:
                g_factor = gain_pos
                direction = 180
            else:
                if self.gforce_effect_adv_curve.get("enable_neg"):
                    g_factor = -gain_neg
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
                adjuster_cpOy = int(-g_factor * 4096)

                if adv_spr:
                    # If being called by advanced spring effect, don't apply adjuster offset here, return offset value and let the advanced spring adjuster effect do it
                    return adjuster_cpOy
                else:
                    if not g_factor:
                        self.effects["gforce_spr"].stop()
                        return

                    offset_adjuster : HapticEffect = self.effects["gforce_spr"].spring_adjuster()
                    cond_x = FFBReport_SetCondition(parameterBlockOffset=0)
                    cond_y = FFBReport_SetCondition(parameterBlockOffset=1)

                    # Adjuster parameters restored to the original pre-refactor
                    # (field-proven) usage: coefficients stay 0 so the active
                    # spring's gain is untouched, saturation stays full scale,
                    # cpOffset carries the G-based center shift.  A refactor-era
                    # change to coefficient=4096/saturation=1 clamped the
                    # adjusted spring's output to ~zero - all spring force died
                    # the moment the effect started.
                    cond_x.set_offset(0)
                    cond_x.positiveSaturation = cond_x.negativeSaturation = 4096
                    cond_y.set_offset(adjuster_cpOy)
                    cond_y.positiveSaturation = cond_y.negativeSaturation = 4096

                    offset_adjuster.setCondition(cond_y).setCondition(cond_x).start()

        else:
            self.__gforce_dispose_all()
            return