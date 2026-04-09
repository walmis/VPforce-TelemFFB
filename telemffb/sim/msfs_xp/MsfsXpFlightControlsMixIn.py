from typing import override
import json
import logging
import math
from math import atan2, sin, sqrt

from telemffb.sim.base.AoAEffectsMixIn import AoAEffectsMixIn
from telemffb.sim.msfs_xp.MfsfXpSteeringFrictionEffectMixIn import MfsfXpSteeringFrictionEffectMixIn
import telemffb.utils as utils
from telemffb.SettingsManager import SpringModeEnum
from telemffb.hw.ffb_rhino import HapticEffect
from telemffb.sim.msfs_xp.MsfsXpFBWFlightControlsMixIn import MsfsXpFBWFlightControlsMixIn
from telemffb.util.Vector import Vector, Vector2D
from telemffb.util.conversions import P0, deg, math, ms2kt, rad, std_air_pressure, vsound
from telemffb.utils import clamp
from telemffb.sim.BaseTelemetryData import BaseTelemetryData

class MsfsXpFlightControlsMixIn(MfsfXpSteeringFrictionEffectMixIn, MsfsXpFBWFlightControlsMixIn, AoAEffectsMixIn):
    """Mixin for MSFS and X-Plane specific flight control handling."""

    # user parameters
    max_elevator_coeff = 0.5
    max_aileron_coeff = 0.5
    max_rudder_coeff = 0.5
    aileron_spring_gain = 0.25
    elevator_spring_gain = 0.25
    rudder_spring_gain = 0.25
    elevator_prop_flow_ratio = 1.0  # how much air flow the elevator receives from the propeller

    rudder_prop_flow_ratio = 1.0
    uncoordinated_turn_effect_enabled: int = 1
    prop_diameter = 1.5

    vne_override: int = 0
    elevator_droop_moment = 0.1  # in FFB force units

    aileron_expo: int = 0
    elevator_expo: int = 0
    rudder_expo: int = 0

    trim_following = False
    local_disable_axis_control = False
    lateral_force_gain = 0.2

    controls_lock_enable = False
    controls_lock_simvar = ''
    controls_lock_simvar_invert = False

    ## end of user parameters

    g_force_gain = 0.1  # this appears constant, not set anywhere else?

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.__dyn_pressure_scale = 0.005  # scale the dynamic pressure to ffb friendly values

        self.use_fbw_for_ap_follow = True
        self.slip_gain = 1.0        
        
        self.aileron_gain = 0.1
        self.elevator_gain = 0.1
        self.rudder_gain = 0.1

        self.rudder_force_dampener = utils.Dampener()
        self.const_force = HapticEffect().constant(0, 0)

    def _sync_controls_lock_simvar(self):
        """Subscribe the ControlsLock simvar once and re-subscribe only when the binding changes."""
        if not self._sim_is_msfs() or not self.controls_lock_enable or not self.controls_lock_simvar:
            return
        # Always call anything_has_changed to initialize tracking state (avoid short-circuit).
        simvar_changed = self.anything_has_changed('controls_lock_simvar', self.controls_lock_simvar)
        enable_changed = self.anything_has_changed('controls_lock_enable', self.controls_lock_enable)
        if 'ControlsLock' not in self._simconnect.sv_dict or simvar_changed or enable_changed:
            self._simconnect.add_simvar(name="ControlsLock", var=self.controls_lock_simvar, sc_unit="enum")
            self._simconnect._resubscribe()

    def _get_msfs_axis_config(self, axis: str, default_var: str, default_range: int = 16384) -> tuple[str, int | float]:
        """Return (var_name, range) for an MSFS axis, respecting custom axis overrides."""
        if axis == 'x' and self.enable_custom_x_axis:
            return self.custom_x_axis, self.raw_x_axis_scale
        if axis == 'y' and self.enable_custom_y_axis:
            return self.custom_y_axis, self.raw_y_axis_scale
        return default_var, default_range

    def _scale_msfs_axis_value(self, value: float, axis_range: int | float, scale: float) -> int | float:
        """Scale a normalized axis value for MSFS and return it."""
        pos = utils.scale(value, (-1, 1), (-axis_range * scale, axis_range * scale))
        if axis_range != 1:
            pos = -int(pos)
        else:
            pos = round(pos, 5)
        return pos

    def _send_msfs_axis_value(self, var: str, value: float, axis_range: int | float, scale: float) -> int | float:
        """Scale a normalized axis value and send it to MSFS.

        Returns the scaled position that was sent, for callers that need to cache it.
        """
        pos = self._scale_msfs_axis_value(value, axis_range, scale)
        self._simconnect.send_event_to_msfs(var, pos)
        return pos

    def _get_controls_lock_state(self, telem_data: BaseTelemetryData) -> bool:
        """Read and normalize the ControlsLock value, applying inversion if configured."""
        if not self.controls_lock_enable:
            return False
        controls_locked = bool(telem_data.get("ControlsLock", 0))
        if self.controls_lock_simvar_invert:
            controls_locked = not controls_locked
        return controls_locked

    def _lock_effects_started(self) -> bool:
        return self.effects['lock_1'].started or self.effects['lock_2'].started

    def _stop_lock_effects(self):
        self.effects['lock_1'].stop()
        self.effects['lock_2'].stop()

    def _prepare_controls_lock(
        self,
        telem_data: BaseTelemetryData,
        controls_locked: bool,
        *,
        set_locked_telemetry: bool = False,
    ) -> bool | None:
        """Centralize lock-state short-circuit so axis handlers only manage axis-specific setup.

        Returns:
            False: lock mode inactive, caller should continue normal axis handling
            True: lock mode fully owns this frame, caller should return immediately
            None: lock mode active and caller must continue with axis-specific spring/detent setup
        """
        if not controls_locked:
            self._stop_lock_effects()
            return False

        if set_locked_telemetry:
            telem_data._controls_locked = True

        if self._lock_effects_started():
            return True

        return None

    def _engage_controls_lock_detents(
        self,
        spring_condition,
        *,
        spring_offset: int,
        detent_1: dict,
        detent_2: dict,
        stop_control_weight: bool = False,
    ):
        """Share the common lock spring + detent startup sequence; callers provide only axis-specific geometry."""
        if stop_control_weight:
            self.effects['control_weight'].stop()

        spring_condition.set_coefficient(4096)
        spring_condition.cpOffset = spring_offset
        self._spring_handle.setCondition(spring_condition)
        self._spring_handle.start()
        self.effects['lock_1'].detent(**detent_1).start()
        self.effects['lock_2'].detent(**detent_2).start()
        self._spring_handle.stop()

    def _apply_joystick_controls_lock(self, telem_data: BaseTelemetryData, controls_locked):
        """Apply 2D joystick/cyclic controls lock with centering detents.

        Returns True if lock mode consumed the frame, False if normal processing should continue.
        """
        lock_result = self._prepare_controls_lock(telem_data, controls_locked, set_locked_telemetry=True)
        if lock_result is True:
            return True
        if lock_result is None:
            phys_x, phys_y = self._get_device_axes()

            self.spring_y.set_coefficient(4096)
            self.spring_x.set_coefficient(4096)
            self.spring_y.cpOffset = 0
            self.spring_x.cpOffset = 0
            self._spring_handle.setCondition(self.spring_y)
            self._spring_handle.setCondition(self.spring_x)
            self._spring_handle.start()

            if (-0.15 < phys_x < 0.15) and (-0.15 < phys_y < 0.15):
                detent_params = dict(peak_x=4096, range_x=4096, gate_pos_y=0, gate_neg_y=0,
                                     peak_y=4096, range_y=4096, gate_pos_x=0, gate_neg_x=0)
                self._engage_controls_lock_detents(
                    self.spring_x,
                    spring_offset=0,
                    detent_1=dict(position_x=1500, position_y=1500, **detent_params),
                    detent_2=dict(position_x=-1500, position_y=-1500, **detent_params),
                    stop_control_weight=True,
                )
                telem_data["_controls_locked"] = controls_locked

            return True
        return False

    def _apply_pedal_controls_lock(self, telem_data: BaseTelemetryData, controls_locked):
        """Apply 1D pedal controls lock with centering detents.

        Returns True if lock mode consumed the frame, False if normal processing should continue.
        """
        lock_result = self._prepare_controls_lock(telem_data, controls_locked)
        if lock_result is True:
            return True
        if lock_result is None:
            phys_x, _ = self._get_device_axes()

            self.spring_x.set_coefficient(4096)
            self.spring_x.cpOffset = 0
            self._spring_handle.setCondition(self.spring_x)
            self._spring_handle.start()

            if -0.15 < phys_x < 0.15:
                detent_params = dict(peak_x=4096, range_x=4096, gate_pos_y=0, gate_neg_y=0)
                self._engage_controls_lock_detents(
                    self.spring_x,
                    spring_offset=0,
                    detent_1=dict(position_x=1500, **detent_params),
                    detent_2=dict(position_x=-1500, **detent_params),
                )
                telem_data["_controls_locked"] = controls_locked

            return True
        return False

    @override
    def on_timeout(self):
        """Handle a timeout event from the main loop.

        This hook is called periodically by the parent class when a timeout occurs
        (for example to stop or refresh time-limited effects).

        Parameters
        ----------
        None

        Returns
        -------
        None

        Side effects
        ------------
        - Calls the parent implementation.
        - Stops any active constant haptic force stored in `self.const_force`.
        """

        super().on_timeout()
        self.const_force.stop()

    @override
    def on_telemetry(self, telem_data: BaseTelemetryData):
        """Process a new telemetry update.

        This method is invoked whenever new telemetry is available from the
        simulator. It performs generic processing in the superclass and then
        forwards the telemetry to `msfs_update_flight_controls` to update
        force-feedback state.

        Parameters
        ----------
        telem_data : dict
            A dictionary containing the latest telemetry values from the
            simulator. Expected keys used by this mixin include (but are not
            limited to): 'IAS', 'Incidence', 'AccBody', 'G', 'WeightOnWheels',
            'FFBType', 'APMaster'/'APServos', and control deflection values.

        Returns
        -------
        None
        """

        super().on_telemetry(telem_data)
        self.msfs_update_flight_controls(telem_data)

    @override
    def ac_modify_game_spring(self):
        """This function is not used in MSFS/X-Plane mixin."""

    def _calculate_airspeeds(self, telem_data: BaseTelemetryData, incidence_vec):
        """Calculate and store airspeed values in telemetry data."""
        _airspeed = telem_data.IAS
        telem_data.TAS = _airspeed
        telem_data.TAS_kt = _airspeed * ms2kt
        telem_data.IAS_kt = telem_data.IAS * ms2kt
        telem_data.AccBody_ms = [x * 9.80665 for x in telem_data.AccBody]
        return _airspeed


    def _calculate_angles(self, telem_data: BaseTelemetryData, incidence_vec):
        """Calculate angle-of-attack (AoA), slip angle and rudder deflection.

        Parameters
        ----------
        telem_data : dict
            Telemetry dictionary which will be updated with calculated values
            (for example 'SideSlip' and 'AoA').
        incidence_vec : telemffb.util.Vector.Vector
            A 3D vector representing the incidence (local airflow) reported by
            the simulator. The vector components must be accessible as
            `x`, `y`, `z` attributes.

        Returns
        -------
        tuple
            A tuple containing (rudder_angle, slip_angle, aoa) where:
            - rudder_angle is the aircraft rudder deflection in radians (sign
              adjusted for X-Plane),
            - slip_angle is the lateral slip angle in radians,
            - aoa is the angle-of-attack in degrees (negative sign preserved
              to match existing code behaviour).
        """
        rudder_angle = telem_data.RudderDefl * rad
        if self._sim_is_xplane():
            rudder_angle = -rudder_angle

        slip_angle = atan2(incidence_vec.x, incidence_vec.z)
        telem_data.SideSlip = slip_angle * deg

        _aoa = -atan2(incidence_vec.y, incidence_vec.z) * deg
        telem_data.AoA = _aoa

        return rudder_angle, slip_angle, _aoa

    def _calculate_prop_airflow(self, telem_data: BaseTelemetryData, _airspeed, incidence_vec):
        """Calculate propeller-induced airflow velocity and elevator AoA.

        Parameters
        ----------
        telem_data : dict
            Telemetry dictionary that may contain 'PropThrust' and will be
            updated with computed values: '_prop_thrust', '_prop_air_vel' and
            '_elevator_aoa'.
        _airspeed : float
            The free-stream airspeed (m/s) previously computed for the
            telemetry sample.
        incidence_vec : telemffb.util.Vector.Vector
            The incidence vector used to compute local angle components.

        Returns
        -------
        float
            The computed propeller-induced air velocity (m/s).
        """
        _prop_thrust = telem_data.PropThrust or 0
        if isinstance(_prop_thrust, list):
            _prop_thrust = max(_prop_thrust)
        if _prop_thrust < 0:
            _prop_thrust = 0

        _prop_air_vel = sqrt(
            2 * _prop_thrust / (telem_data.AirDensity * (math.pi * (self.prop_diameter / 2) ** 2)) + _airspeed**2
        )

        telem_data._prop_thrust = _prop_thrust

        if abs(incidence_vec.y) > 0.5 or _prop_air_vel > 1:
            _elevator_aoa = atan2(-incidence_vec.y, _prop_air_vel) * deg
        else:
            _elevator_aoa = 0
        telem_data._elevator_aoa = _elevator_aoa

        telem_data._prop_air_vel = _prop_air_vel
        return _prop_air_vel

    def _calculate_dynamic_pressures(self, telem_data: BaseTelemetryData, _prop_air_vel):
        """Calculate scaled dynamic pressures used to compute FFB gains.

        Parameters
        ----------
        telem_data : dict
            Telemetry dict expected to contain 'DynPressure' and 'AirDensity'.
        _prop_air_vel : float
            Velocity generated by propeller flow (m/s).

        Returns
        -------
        tuple
            (_elev_dyn_pressure, _dyn_pressure, _rud_dyn_pressure)
            All values are scaled by the internal dynamic pressure scale
            (`self.__dyn_pressure_scale`).
        """
        _elev_dyn_pressure = (
            utils.mix(
                telem_data.DynPressure,
                0.5 * telem_data.AirDensity * _prop_air_vel**2,
                self.elevator_prop_flow_ratio,
            )
            * self.__dyn_pressure_scale
        )
        telem_data._elev_dyn_pressure = _elev_dyn_pressure

        _dyn_pressure = telem_data.DynPressure * self.__dyn_pressure_scale

        _rud_dyn_pressure = (
            utils.mix(
                telem_data.DynPressure,
                0.5 * telem_data.AirDensity * _prop_air_vel**2,
                self.rudder_prop_flow_ratio,
            )
            * self.__dyn_pressure_scale
        )

        return _elev_dyn_pressure, _dyn_pressure, _rud_dyn_pressure

    def _calculate_vne_and_gains(self, telem_data: BaseTelemetryData):
        """Calculate Vne (never-exceed airspeed) and set aerodynamic gains.

        The method computes an estimated Vne based on simulator-supplied
        design speeds or X-Plane values, stores supporting telemetry fields
        and updates `self.elevator_gain`, `self.aileron_gain` and
        `self.rudder_gain` used by downstream calculations.

        Parameters
        ----------
        telem_data : dict
            Telemetry dict. For MSFS this method expects a 'DesignSpeed' entry
            (tuple of speeds). For X-Plane it may read 'Vne' or related fields.

        Returns
        -------
        tuple
            (vne, Q_gain) where vne is the computed Vne in m/s and Q_gain is a
            normalization factor applied to dynamic pressure values.
        """
        if telem_data.src == "XPLANE":
            vne = telem_data.Vne
            vs0 = telem_data.Vso
        else:
            vc, vs0, vs1 = telem_data.DesignSpeed
            telem_data.Vc_kt = vc * ms2kt
            Tvne = vc * 1.4
            qv = 0.5 * std_air_pressure * (Tvne**2)
            kmNs = ((qv / P0) + 1) ** (2 / 7)
            vne = vsound * sqrt(5 * (kmNs - 1))

        telem_data.Vne_ms_calc = vne
        if isinstance(self.vne_override, (float, int)):
            if self.vne_override:
                vne = self.vne_override

        telem_data.Vne_kt = vne * ms2kt

        Qvne = 0.5 * std_air_pressure * vne**2
        telem_data.Qvne = Qvne * self.__dyn_pressure_scale

        Q_gain = 1 / (Qvne * self.__dyn_pressure_scale)
        telem_data.Qvc_gain = Q_gain

        self.elevator_gain = Q_gain
        self.aileron_gain = Q_gain
        self.rudder_gain = Q_gain

        return vne, Q_gain

    def _calculate_control_coefficients(self, telem_data: BaseTelemetryData, _elev_dyn_pressure, _dyn_pressure, _rud_dyn_pressure, _slip_gain, g_force):
        """Calculate force coefficients for elevator, aileron and rudder.

        Parameters
        ----------
        telem_data : dict
            Telemetry dictionary (may be read/written for debug fields).
        _elev_dyn_pressure : float
            Scaled dynamic pressure acting on the elevator surface.
        _dyn_pressure : float
            General scaled dynamic pressure used for aileron calculations.
        _rud_dyn_pressure : float
            Scaled dynamic pressure acting on the rudder surface.
        _slip_gain : float
            Slip-dependent scalar used to reduce control authority during
            uncoordinated flight.
        g_force : float
            Current G-load reported by telemetry (used for elevator droop).

        Returns
        -------
        tuple
            (elevator_coeff, aileron_coeff, rudder_coeff, elevator_droop_term)
        """
        # Elevator droop effect
        _elevator_droop_term = self.elevator_droop_moment * g_force / (1 + _elev_dyn_pressure)
        telem_data._elevator_droop_term = _elevator_droop_term

        # Calculate raw coefficients
        aileron_coeff = _dyn_pressure * self.aileron_gain * _slip_gain
        elevator_coeff = _elev_dyn_pressure * self.elevator_gain * _slip_gain
        rudder_coeff = _rud_dyn_pressure * self.rudder_gain * _slip_gain

        # Apply expo curve or advanced spring gains
        if self.spring_mode_is(SpringModeEnum.ADVANCED):
            #print(self.adv_spr_gains)
            adv_spr_stgs = self.adv_spr_gains
            scale = adv_spr_stgs.get("scale")
            spd_y = scale * elevator_coeff
            spd_x = scale * aileron_coeff
            y_gains = utils.get_gain_from_speed(self.adv_spr_gains, spd_y)
            x_gains = utils.get_gain_from_speed(self.adv_spr_gains, spd_x)
            elevator_coeff = y_gains.get("y")
            aileron_coeff = x_gains.get("x")

            # Rudder coefficient with advanced spring
            spd_x_rud = scale * rudder_coeff
            x_gains_rud = utils.get_gain_from_speed(self.adv_spr_gains, spd_x_rud)
            rudder_coeff = x_gains_rud.get("x")
        else:
            elevator_coeff = utils.expocurve(elevator_coeff, self.elevator_expo)
            aileron_coeff = utils.expocurve(aileron_coeff, self.aileron_expo)
            rudder_coeff = utils.expocurve(rudder_coeff, self.rudder_expo)

        telem_data._elev_coeff = elevator_coeff
        telem_data._aile_coeff = aileron_coeff
        telem_data._rud_coeff = rudder_coeff

        return elevator_coeff, aileron_coeff, rudder_coeff, _elevator_droop_term

    def _calculate_g_term(self, telem_data: BaseTelemetryData):
        """Calculate the term used to translate body-acceleration to a pitch bias.

        Reads input device axis scaling to weight the G effect by how much the
        user is gripping/holding the stick. The returned term is written to
        telemetry under '_G_term'.

        Parameters
        ----------
        telem_data : dict
            Telemetry dictionary used to store the resulting '_G_term'.

        Returns
        -------
        float
            The computed G-term used as a constant pitch bias in the FFB model.
        """
        assert HapticEffect.device is not None, "HapticEffect.device is not initialized"

        input_data = HapticEffect.device.get_input()
        dx, dy = input_data.CP_scaled_axisXY()
        _G_term = self.g_force_gain * telem_data.AccBody[1]
        _G_term = _G_term * abs(dy)
        telem_data._G_term = _G_term
        return _G_term

    def _calculate_rudder_force(self, telem_data: BaseTelemetryData, slip_angle, rudder_angle, _dyn_pressure, _slip_gain, vne):
        """Calculate rudder feedback force based on side-slip and rudder deflection.

        Parameters
        ----------
        telem_data : dict
            Telemetry dictionary providing 'IAS' and other state used to scale
            the resulting force.
        slip_angle : float
            Side-slip angle in radians.
        rudder_angle : float
            Current rudder deflection in radians (sign adjusted for X-Plane).
        _dyn_pressure : float
            Scaled dynamic pressure used for force magnitude calculation.
        _slip_gain : float
            Slip-based reduction factor.
        vne : float
            Never-exceed speed in m/s used to scale effect strength by speed.

        Returns
        -------
        float
            A clamped rudder force in the range [-1, 1] that may be time-damped
            by an internal dampener.
        """
        rud = (slip_angle - rudder_angle) * _dyn_pressure * _slip_gain
        rud_force = clamp((rud * self.rudder_gain), -1, 1)
        rud_force = self.rudder_force_dampener.update(rud_force, derivative_hz=5, derivative_k=0.015)

        IAS = telem_data.IAS
        speed_factor = utils.scale_clamp(IAS, (0, vne), (0.0, 1.0))
        rud_force = rud_force * speed_factor

        return rud_force

    def _calculate_joystick_trim_offsets(self, telem_data: BaseTelemetryData, ap_active):
        """Calculate trim-following offsets for joystick axes.

        Parameters
        ----------
        telem_data : dict
            Telemetry dictionary containing trim percentages (e.g. 'ElevTrimPct',
            'AileronTrimPct') and autopilot/servo positions used when AP is
            following.
        ap_active : bool
            True when autopilot is engaged; used to select different sources
            for the current control positions.

        Returns
        -------
        tuple
            (phys_stick_x_offs, virtual_stick_x_offs, phys_stick_y_offs, virtual_stick_y_offs)
            where physical offsets are integer device offsets (0..4096) and
            virtual offsets are normalized [-1..1] values applied to virtual
            stick commands.
        """
        if not (self.trim_following and self.telemffb_controls_axes and not self.local_disable_axis_control):
            return 0, 0, 0, 0

        elev_trim = telem_data.ElevTrimPct or 0
        aileron_trim = telem_data.AileronTrimPct or 0

        aileron_trim = clamp(aileron_trim * self.joystick_trim_follow_gain_physical_x, -1, 1)
        virtual_stick_x_offs = aileron_trim - (aileron_trim * self.joystick_trim_follow_gain_virtual_x)

        elev_trim = clamp(elev_trim * self.joystick_trim_follow_gain_physical_y, -1, 1)
        elev_trim = self.elev_trim_dampener.update(elev_trim, derivative_hz=5, derivative_k=0.15)

        virtual_stick_y_offs = elev_trim - (elev_trim * self.joystick_trim_follow_gain_virtual_y)
        phys_stick_y_offs = int(elev_trim * 4096)

        if self.ap_following and ap_active:
            if self._sim_is_msfs():
                aileron_pos = telem_data.AileronDeflPctLR or (0, 0)
                aileron_pos = aileron_pos[0]
                aileron_pos = self.aileron_pos_dampener.update(aileron_pos, derivative_hz=5, derivative_k=0.15)
            elif self._sim_is_xplane():
                aileron_pos = telem_data.APRollServo or 0
            else:
                aileron_pos = 0
            phys_stick_x_offs = int(aileron_pos * 4096)
        else:
            phys_stick_x_offs = int(aileron_trim * 4096)

        return phys_stick_x_offs, virtual_stick_x_offs, phys_stick_y_offs, virtual_stick_y_offs

    def _send_joystick_axis_commands(self, telem_data: BaseTelemetryData, virtual_stick_x_offs, virtual_stick_y_offs):
        """Send axis control commands to the simulator for a physical joystick.

        Parameters
        ----------
        telem_data : dict
            Telemetry dictionary; the method will read/write 'phys_x' and
            'phys_y' entries to expose raw device positions.
        virtual_stick_x_offs : float
            Virtual offset applied to the physical X axis (normalized).
        virtual_stick_y_offs : float
            Virtual offset applied to the physical Y axis (normalized).

        Returns
        -------
        None
        """
        if not (self.telemffb_controls_axes and not self.local_disable_axis_control):
            return
        assert HapticEffect.device is not None, "HapticEffect.device is not initialized"
        phys_x, phys_y = self._get_device_axes()
        telem_data.phys_x = phys_x
        telem_data.phys_y = phys_y

        x_pos = phys_x - virtual_stick_x_offs
        y_pos = phys_y - virtual_stick_y_offs

        x_scale = clamp(self.joystick_x_axis_scale, 0, 1)
        y_scale = clamp(self.joystick_y_axis_scale, 0, 1)

        if self._sim_is_xplane():
            pos_x_pos = x_pos * x_scale
            pos_y_pos = y_pos * y_scale
            self.send_xp_command(f"AXIS:jx={round(pos_x_pos, 5)},jy={round(pos_y_pos, 5)}")

        if self._sim_is_msfs():
            x_var, x_range = self._get_msfs_axis_config('x', "AXIS_AILERONS_SET")
            y_var, y_range = self._get_msfs_axis_config('y', "AXIS_ELEVATOR_SET")
            self._send_msfs_axis_value(x_var, x_pos, x_range, x_scale)
            self._send_msfs_axis_value(y_var, y_pos, y_range, y_scale)

    def _calculate_aoa_offset(self, telem_data: BaseTelemetryData, _aoa, force_trim_y_offset, phys_stick_y_offs, IAS, vne):
        """Calculate Y offset for AoA effect."""
        if (
            self.aoa_effect_enabled
            and (telem_data.ElevDeflPct or 0) != 0
            and not max(telem_data.WeightOnWheels)
        ):
            tot = telem_data.ElevDefl / telem_data.ElevDeflPct
            speed_factor = utils.scale_clamp(IAS, (0, vne), (0.0, 1.0))
            y_offs = _aoa / tot
            y_offs = y_offs + force_trim_y_offset + (phys_stick_y_offs / 4096)
            y_offs = clamp(y_offs, -1, 1)
            y_offs = int(y_offs * 4096 * speed_factor * self.aoa_effect_gain)
        else:
            y_offs = force_trim_y_offset + (phys_stick_y_offs / 4096)
            y_offs = clamp(y_offs, -1, 1)
            y_offs = int(y_offs * 4096)

        return y_offs

    def _calculate_joystick_spring_coefficients(self, telem_data: BaseTelemetryData, elevator_coeff, aileron_coeff, base_elev_coeff, base_ailer_coeff):
        """Calculate spring coefficients for joystick axes."""
        max_coeff_y = int(4096 * self.max_elevator_coeff)
        realtime_coeff_y = int(4096 * elevator_coeff)
        ec = int(utils.scale_clamp(realtime_coeff_y, (base_elev_coeff, 4096), (base_elev_coeff, max_coeff_y)))

        pct_max_e = ec / max_coeff_y
        telem_data._pct_max_e = pct_max_e
        self._ipc_telem["_pct_max_e"] = pct_max_e
        logging.debug(f"Elev Coef: {ec}")
        telem_data._ec = ec

        max_coeff_x = int(4096 * self.max_aileron_coeff)
        realtime_coeff_x = int(4096 * aileron_coeff)
        ac = int(utils.scale_clamp(realtime_coeff_x, (base_ailer_coeff, 4096), (base_ailer_coeff, max_coeff_x)))

        pct_max_a = ac / max_coeff_x
        telem_data._pct_max_a = pct_max_a
        self._ipc_telem["_pct_max_a"] = pct_max_a
        telem_data._ac = ac
        logging.debug(f"Ailer Coef: {ac}")

        return ec, ac

    def _apply_joystick_constant_forces(self, telem_data: BaseTelemetryData, _elevator_droop_term, _G_term):
        """Apply constant forces (droop, G-forces, lateral) to joystick."""
        cf_pitch = -_elevator_droop_term - _G_term
        cf_pitch = clamp(cf_pitch, -1.0, 1.0)

        if self.uncoordinated_turn_effect_enabled:
            _side_accel = -telem_data.AccBody[0] * self.lateral_force_gain
        else:
            _side_accel = 0

        cf_roll = _side_accel

        cf = Vector2D(cf_pitch, cf_roll)
        if cf.magnitude() > 1.0:
            cf = cf.normalize()

        mag, theta = cf.to_polar()
        self.effects["control_weight"].constant(mag, theta * deg).envelope(
             attackFromForce=0,       # Start from zero for noticeable fade-in
             attackTime=1000,          # 1000ms attack
         ).start()

    def _update_joystick_controls(self, telem_data: BaseTelemetryData, ap_active, elevator_coeff, aileron_coeff, 
                                   base_elev_coeff, base_ailer_coeff, _aoa, _elevator_droop_term, 
                                   _G_term, force_trim_y_offset, IAS, vne, controls_locked):
        """Handle all joystick-specific FFB updates."""
        phys_stick_x_offs, virtual_stick_x_offs, phys_stick_y_offs, virtual_stick_y_offs = \
            self._calculate_joystick_trim_offsets(telem_data, ap_active)

        self._send_joystick_axis_commands(telem_data, virtual_stick_x_offs, virtual_stick_y_offs)

        y_offs = self._calculate_aoa_offset(telem_data, _aoa, force_trim_y_offset, phys_stick_y_offs, IAS, vne)
        self.spring_y.cpOffset = y_offs
        self.spring_x.cpOffset = phys_stick_x_offs

        ec, ac = self._calculate_joystick_spring_coefficients(telem_data, elevator_coeff, aileron_coeff, 
                                                               base_elev_coeff, base_ailer_coeff)

        self.spring_y.set_coefficient(ec)
        self.spring_x.set_coefficient(ac)

        if self._apply_joystick_controls_lock(telem_data, controls_locked):
            return

        self._spring_handle.setCondition(self.spring_y)
        self._spring_handle.setCondition(self.spring_x)

        self._apply_joystick_constant_forces(telem_data, _elevator_droop_term, _G_term)

        self._spring_handle.start()

    def _calculate_rudder_trim_offsets(self, telem_data: BaseTelemetryData):
        """Calculate trim following offsets for rudder pedals."""
        if not (self.trim_following and self.telemffb_controls_axes and not self.local_disable_axis_control):
            return 0, 0

        rudder_trim = telem_data.RudderTrimPct or 0
        rudder_trim = clamp(rudder_trim * self.rudder_trim_follow_gain_physical_x, -1, 1)
        virtual_rudder_x_offs = rudder_trim - (rudder_trim * self.rudder_trim_follow_gain_virtual_x)
        phys_rudder_x_offs = int(rudder_trim * 4096)

        return phys_rudder_x_offs, virtual_rudder_x_offs

    def _calculate_rudder_spring_coefficient(self, telem_data: BaseTelemetryData, rudder_coeff, base_rudder_coeff):
        """Calculate spring coefficient for rudder pedals."""
        if self.spring_mode_is(SpringModeEnum.ADVANCED):
            if not self.adv_spr_gains:
                self.flag_error("Please open and configure the advanced spring gain settings")
                rudder_coeff = 0
            else:
                gains = utils.get_gain_from_speed(self.adv_spr_gains, telem_data.IAS or 0)
                self.spring_x.set_coefficient(gains.get("x", 0))
                rudder_coeff = gains.get("x", 0)
        else:
            max_coeff_x = int(4096 * self.max_rudder_coeff)
            realtime_coeff_x = int(4096 * rudder_coeff)
            rudder_coeff = int(utils.scale_clamp(realtime_coeff_x, (base_rudder_coeff, 4096), (base_rudder_coeff, max_coeff_x)))

            pct_max_r = rudder_coeff / max_coeff_x
            telem_data._pct_max_r = pct_max_r
            self._ipc_telem["_pct_max_r"] = pct_max_r
            telem_data._rc = rudder_coeff
            self.spring_x.set_coefficient(rudder_coeff)

        return rudder_coeff
    
    @override
    def msfs_update_steering_friction_effect(self, telem_data: BaseTelemetryData):
        pass

    def _apply_steering_friction(self, telem_data: BaseTelemetryData, phys_rudder_x_offs, rudder_coeff):
        """Apply steering friction effect when on ground."""
        if not self.steering_friction:
            return

        on_ground = telem_data.SimOnGround or 0
        wos = (telem_data.WeightOnWheels or [0])[0]
        surface = telem_data.SurfaceType or 0

        if on_ground and (wos or surface == "Water"):
            rudder_angle = 30  # assumed rudder travel
            dynamic_angle = phys_rudder_x_offs * rudder_angle / 4096
            dynamic_force = rudder_coeff / 4096
            csa = telem_data.CenterSteerAnglePct or 0
            steer_angle = csa * rudder_angle
            steer_force = self.steering_friction_spring / 40

            wr = telem_data.WaterRudderExt or 0
            if surface == "Water":
                steer_force *= wr

            result_angle_percent, result_mag = utils.add_vectors_deg(
                dynamic_angle, dynamic_force, steer_angle, steer_force
            )

            self.spring_x.set_coefficient(result_mag, True)
            self.spring_x.set_offset(result_angle_percent / rudder_angle)

    def _send_rudder_axis_commands(self, telem_data: BaseTelemetryData, virtual_rudder_x_offs):
        """Send axis control commands to the simulator for rudder pedals."""
        if not (self.telemffb_controls_axes and not self.local_disable_axis_control):
            return
        
        assert HapticEffect.device is not None, "HapticEffect.device is not initialized"

        phys_x, phys_y = self._get_device_axes()
        telem_data.phys_x = phys_x
        x_pos = phys_x - virtual_rudder_x_offs
        x_scale = clamp(self.rudder_x_axis_scale, 0, 1)

        if self._sim_is_xplane():
            pos_x_pos = x_pos * x_scale
            self.send_xp_command(f"AXIS:px={round(pos_x_pos, 5)}")

        if self._sim_is_msfs():
            x_var, x_range = self._get_msfs_axis_config('x', "AXIS_RUDDER_SET")
            self._send_msfs_axis_value(x_var, x_pos, x_range, x_scale)

    def _update_pedals_controls(self, telem_data: BaseTelemetryData, rudder_coeff, base_rudder_coeff, rud_force, controls_locked):
        """Handle all pedals-specific FFB updates."""

        phys_rudder_x_offs, virtual_rudder_x_offs = self._calculate_rudder_trim_offsets(telem_data)

        rc = self._calculate_rudder_spring_coefficient(telem_data, rudder_coeff, base_rudder_coeff)

        self.spring_x.cpOffset = phys_rudder_x_offs

        self._apply_steering_friction(telem_data, phys_rudder_x_offs, rc)

        if self._apply_pedal_controls_lock(telem_data, controls_locked):
            return

        self._spring_handle.setCondition(self.spring_x)

        self._send_rudder_axis_commands(telem_data, virtual_rudder_x_offs)

        self.const_force.constant(rud_force, 270).start()



        self._spring_handle.start()
    

    def msfs_update_flight_controls(self, telem_data: BaseTelemetryData):
        """
        Main method for updating flight controls. 
        Calculations loosely based on FlightGear FFB:
        https://wiki.flightgear.org/Force_feedback
        https://github.com/viktorradnai/fg-haptic/blob/master/force-feedback.nas
        """
        self._spring_handle.name = "dynamic_spring"
        
        # Determine autopilot state
        if self._sim_is_msfs():
            ap_active = telem_data.APMaster or 0
        elif self._sim_is_xplane():
            ap_active = telem_data.APServos or 0
        else:
            return

        ffb_type = telem_data.FFBType or "joystick"


        self._sync_controls_lock_simvar()
        controls_locked = self._get_controls_lock_state(telem_data)

        if ffb_type == "collective":
            return


        # Early exit conditions for FBW or helicopter mode
        if self.spring_mode_is(SpringModeEnum.FBW) or telem_data.ACisFBW:
            logging.debug("FBW Setting enabled, running fbw_flight_controls")
            self.update_fbw_flight_controls(telem_data)
            return

        if telem_data.AircraftClass == "Helicopter":
            logging.debug("Aircraft is Helicopter, aborting update_flight_controls")
            return

        if self.telemffb_controls_axes and self.ap_following and ap_active and self.use_fbw_for_ap_follow:
            logging.debug("FBW Setting enabled, running fbw_flight_controls")
            self.update_fbw_flight_controls(telem_data, ap=True)
            self.effects["dynamic_spring"].stop()
            return
        else:
            self.effects["fbw_spring"].stop()

        # Determine base gains for spring mode
        elev_base_gain = 0
        ailer_base_gain = 0
        rudder_base_gain = 0
        
        if self.spring_mode_is(SpringModeEnum.CENTER):
            elev_base_gain = self.elevator_spring_gain
            ailer_base_gain = self.aileron_spring_gain
            rudder_base_gain = self.rudder_spring_gain
            logging.debug(
                f"Aircraft controls are center sprung, setting x:y base gain to {ailer_base_gain}:{elev_base_gain}, rudder base gain to {rudder_base_gain}"
            )

        # Calculate aerodynamic values
        incidence_vec = Vector(telem_data.Incidence)
        force_trim_x_offset = self.force_trim_x_offset
        force_trim_y_offset = self.force_trim_y_offset
        
        _airspeed = self._calculate_airspeeds(telem_data, incidence_vec)
        IAS = telem_data.IAS
        
        rudder_angle, slip_angle, _aoa = self._calculate_angles(telem_data, incidence_vec)
        _prop_air_vel = self._calculate_prop_airflow(telem_data, _airspeed, incidence_vec)
        
        # Calculate dynamic pressures
        _elev_dyn_pressure, _dyn_pressure, _rud_dyn_pressure = self._calculate_dynamic_pressures(telem_data, _prop_air_vel)
        
        # Calculate Vne and gains
        vne, Q_gain = self._calculate_vne_and_gains(telem_data)
        
        # Calculate slip gain
        _slip_gain = 1.0 - self.slip_gain * abs(sin(slip_angle))
        telem_data._slip_gain = _slip_gain
        
        g_force = telem_data.G
        
        # Calculate control surface coefficients
        elevator_coeff, aileron_coeff, rudder_coeff, _elevator_droop_term = \
            self._calculate_control_coefficients(telem_data, _elev_dyn_pressure, _dyn_pressure, _rud_dyn_pressure, _slip_gain, g_force)
        
        # Calculate G-force term
        _G_term = self._calculate_g_term(telem_data)
        
        # Calculate rudder force
        rud_force = self._calculate_rudder_force(telem_data, slip_angle, rudder_angle, _dyn_pressure, _slip_gain, vne)
        
        # Calculate base coefficients for scaling
        base_elev_coeff = round(clamp((elev_base_gain * 4096), 0, 4096))
        base_ailer_coeff = round(clamp((ailer_base_gain * 4096), 0, 4096))
        base_rudder_coeff = round(clamp((rudder_base_gain * 4096), 0, 4096))

        # Apply device-specific FFB updates
        if ffb_type == "joystick":
            self._update_joystick_controls(
                telem_data, ap_active, elevator_coeff, aileron_coeff,
                base_elev_coeff, base_ailer_coeff, _aoa, _elevator_droop_term,
                _G_term, force_trim_y_offset, IAS, vne, controls_locked
            )
        elif ffb_type == "pedals":
            self._update_pedals_controls(telem_data, rudder_coeff, base_rudder_coeff, rud_force, controls_locked)
