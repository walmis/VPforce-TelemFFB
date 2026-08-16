import telemffb.utils as utils
from telemffb.SettingsManager import SpringModeEnum
from telemffb.hw.ffb_rhino import HapticEffect
from telemffb.sim.base.AircraftEffectUtilsBase import AircraftEffectUtilsBase
from telemffb.sim.base.DynamicSpringMixin import DynamicSpringMixin
from telemffb.sim.base.AircraftParamsMixIn import AircraftParamsMixIn

from telemffb.sim.base.AdvancedSpringMixIn import AdvancedSpringMixIn
from telemffb.sim.BaseTelemetryData import BaseTelemetryData

class PedalSpringOverrideMixIn(AdvancedSpringMixIn, AircraftParamsMixIn):
    '''Pedal spring override and trimming mixin.'''

    # user parameters
    pedal_spring_gain: float = 1.0
    pedal_trimming_enabled: bool = False
    pedal_dampening_gain = 0

    pedal_force_trim_enabled: bool = False
    pedal_ft_use_master_buttons: bool = False
    pedal_ft_release_button: int = 0
    pedal_ft_reset_button: int = 0
    pedal_ft_damper_enabled: bool = False
    pedal_ft_damper_force: float = 0.0
    # end of user parameters

    def __init__(self):
        super().__init__()
        self.pedal_trim_reset_complete: bool = False
        self.cpO_x = 0
        self.cpO_y = 0

    def ac_update_pedal_trim(self, telem_data: BaseTelemetryData):
        """Update the pedal trim effect based on telemetry data and user input.
        This method should be overridden in subclasses to implement specific pedal trim logic.
        """
        pass

    def ac_update_pedal_force_trim(self, telem_data: BaseTelemetryData, ft_active=True):
        """
        Update the pedal force-trim state and apply changes to the pedal spring.
        This method inspects the current input state and configured trim buttons, then
        either applies an immediate force-trim (setting the spring coefficient and
        center-point offset) or performs a timed trim reset that steps the center-point
        offset back to zero. It returns True when it handled a trim or reset action,
        and False when no action was taken.
        Parameters
        ----------
        telem_data : Any
            Telemetry information (kept for API compatibility). Not used by this
            implementation.
        ft_active : bool, optional
            If False, treat force-trim as active (forces the force-trim branch to run).
            Default True.
        Returns
        -------
        bool
            True if a force-trim or trim-reset action was performed (state changed);
            False if no action was required.
        Side effects
        ------------
        - Reads (phys_x, phys_y) from the device; phys_y is not used.
        - May modify:
          - self.spring_x.set_coefficient(...) (spring stiffness/force coefficient).
          - self.spring_x.cpOffset (center-point offset for the spring).
          - self.cpO_x (cached center-point offset, integer scaled).
          - self.pedal_trim_reset_complete (boolean indicating reset completion).
        - Calls self.check_button_press(...) to detect button events.
        - Calls self.step_value_over_time("center_x", self.cpO_x, 1000, 0) to smoothly
          step cpO_x toward zero during a reset.
        Behavior details
        ----------------
        - If self.is_pedals() is False, the method returns immediately (no-op).
        - Reads (phys_x, phys_y) from the device; phys_y is not used.
        - If the force-trim button is pressed (or ft_active is False):
          - If self.pedal_ft_damper_enabled, set spring coefficient to
            self.pedal_ft_damper_force; otherwise set coefficient to 0.
          - Compute self.cpO_x = round(phys_x * 4096) and write it to
            self.spring_x.cpOffset.
          - Return True.
        - Else if the trim-reset button is pressed or a previous reset is still in
          progress (not self.pedal_trim_reset_complete):
          - Set spring coefficient to 4096 (maximum/default).
          - Call self.step_value_over_time("center_x", self.cpO_x, 1000, 0) to update
            self.cpO_x toward 0, assign the result to self.cpO_x and
            self.spring_x.cpOffset.
          - Set self.pedal_trim_reset_complete to True when cpO_x reaches 0; otherwise
            set it to False.
          - Return True.
        - Otherwise return False.
        Notes
        -----
        - The code uses a 4096 scaling for cpOffset; phys_x is expected to be in the
          device's normalized range (consult device conventions for sign and range).
        - This method directly mutates hardware-related objects and internal flags;
          call sites should be prepared for those side effects.
        """
        if not self.is_pedals(): return

        phys_x, phys_y = self._get_device_axes()

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

    def ac_override_pedal_spring(self, telem_data: BaseTelemetryData):
        if not self.is_pedals(): return
        if self._sim_is_msfs() or self._sim_is_xplane(): # TODO: override ac_override_pedal_spring with a stub in the child class
            return

        if self.spring_mode_is(SpringModeEnum.NONE) or self.spring_mode_is(SpringModeEnum.TELEM):
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
            # DCS UH1 and OH58 export "ForceTrimSwitch" indicating the position of the cockpit master force trim switch.
            # If switch telemetry is present, and switch is off, ac_update_pedal_force_trim will follow path as if the
            # FT button is depressed, simulating the system being disabled.  Default value is True ("switch on") if the
            # telemetry key is absent (as will be for all other helicopters).
            if not self.ac_update_pedal_force_trim(telem_data, ft_active=telem_data.get('ForceTrimSW', True)):
                spring_coeff = utils.clamp(self.pedal_spring_gain, 0, 1.0)
                self.spring_x.set_coefficient(spring_coeff)

        elif self.spring_mode_is(SpringModeEnum.DYNAMIC) or self.spring_mode_is(SpringModeEnum.CUSTOM):
            tas = telem_data.TAS or 0

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
            if self.pedal_trimming_enabled: # no-op by default don't check for DCS, and self._sim_is_dcs():
                self.ac_update_pedal_trim(telem_data)
            # return
        spring = self.effects["pedal_spring"].spring()
        damper_coeff = round(utils.clamp((self.pedal_dampening_gain * 4096), 0, 4096))
        # self.damper = effects["pedal_damper"].damper(coef_x=damper_coeff).start()

        spring.setCondition(self.spring_x)
        spring.start(override=True)

    def verify_pedal_axis(self):
        if not self.is_pedals():
            return False

        x, y = self._get_device_axes()
        if y != 0 and x == 0:
            self.flag_error("Pedal axis mismatch: Y axis used instead of X. Fix by swapping X/Y in VPConfigurator.")

    def on_telemetry(self, telem_data: BaseTelemetryData):
        super().on_telemetry(telem_data)
        self.verify_pedal_axis()
        self.ac_override_pedal_spring(telem_data)