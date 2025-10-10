import telemffb.utils as utils
from telemffb.SettingsManager import SpringModeEnum
from telemffb.hw.ffb_rhino import HapticEffect
from telemffb.sim.msfs_xp.MsfsXpFBWFlightControlsMixIn import MsfsXpFBWFlightControlsMixIn
from telemffb.util.Vector import Vector, Vector2D
from telemffb.util.conversions import P0, deg, math, ms2kt, rad, std_air_pressure, vsound
from telemffb.utils import clamp


import json
import logging
import math
from math import atan2, sin, sqrt


class MsfsXpFlightControlsMixIn(MsfsXpFBWFlightControlsMixIn):
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

    def msfs_update_flight_controls(self, telem_data):
        # calculations loosely based on FLightGear FFB page:
        # https://wiki.flightgear.org/Force_feedback
        # https://github.com/viktorradnai/fg-haptic/blob/master/force-feedback.nas
        self._spring_handle.name = "dynamic_spring"
        if self._sim_is_msfs():
            ap_active = telem_data.get("APMaster", 0)
        elif self._sim_is_xplane():
            ap_active = telem_data.get("APServos", 0)
        else:
            return

        elev_base_gain = 0
        ailer_base_gain = 0
        rudder_base_gain = 0
        ffb_type = telem_data.get("FFBType", "joystick")
        if ffb_type == "collective":
            return

        if self.spring_mode_is(SpringModeEnum.FBW) or telem_data.get("ACisFBW", 0):
            logging.debug("FBW Setting enabled, running fbw_flight_controls")
            self._update_fbw_flight_controls(telem_data)
            return

        if telem_data.get("AircraftClass") == "Helicopter":
            logging.debug("Aircraft is Helicopter, aborting update_flight_controls")
            return

        if self.telemffb_controls_axes and self.ap_following and ap_active and self.use_fbw_for_ap_follow:
            logging.debug("FBW Setting enabled, running fbw_flight_controls")
            self._update_fbw_flight_controls(telem_data, ap=True)
            self.effects["dynamic_spring"].stop()
            return
        else:
            self.effects["fbw_spring"].stop()

        if self.spring_mode_is(SpringModeEnum.CENTER):
            elev_base_gain = self.elevator_spring_gain
            ailer_base_gain = self.aileron_spring_gain
            rudder_base_gain = self.rudder_spring_gain
            logging.debug(
                f"Aircraft controls are center sprung, setting x:y base gain to{ailer_base_gain}:{elev_base_gain}, rudder base gain to {rudder_base_gain}"
            )

        incidence_vec = Vector(telem_data["Incidence"])

        force_trim_x_offset = self.force_trim_x_offset
        force_trim_y_offset = self.force_trim_y_offset

        _airspeed = incidence_vec.z
        _airspeed = telem_data["IAS"]
        telem_data["TAS"] = _airspeed  # why not use simvar AIRSPEED TRUE?
        IAS = telem_data["IAS"]
        telem_data["TAS_kt"] = _airspeed * ms2kt
        telem_data["IAS_kt"] = IAS * ms2kt
        # show acc in m/s
        telem_data["AccBody_ms"] = [x * 9.80665 for x in telem_data["AccBody"]]

        base_elev_coeff = round(clamp((elev_base_gain * 4096), 0, 4096))
        base_ailer_coeff = round(clamp((ailer_base_gain * 4096), 0, 4096))
        base_rudder_coeff = round(clamp((rudder_base_gain * 4096), 0, 4096))

        # logging.info(f"Base Elev/Ailer coeff = {base_elev_coeff}/{base_ailer_coeff}")

        rudder_angle = telem_data["RudderDefl"] * rad  # + trim?
        if self._sim_is_xplane():
            rudder_angle = -rudder_angle

        # print(data["ElevDefl"] / data["ElevDeflPct"] * 100)

        slip_angle = atan2(incidence_vec.x, incidence_vec.z)
        telem_data["SideSlip"] = slip_angle * deg  # overwrite sideslip with our calculated version (including wind)

        g_force = telem_data["G"]  # this includes earths gravity

        _aoa = -atan2(incidence_vec.y, incidence_vec.z) * deg
        telem_data["AoA"] = _aoa

        # calculate air flow velocity exiting the prop
        # based on https://www.grc.nasa.gov/www/k-12/airplane/propth.html
        _prop_thrust = telem_data.get("PropThrust", 0)
        if isinstance(_prop_thrust, list):
            _prop_thrust = max(_prop_thrust)

        if _prop_thrust < 0:
            _prop_thrust = 0

        _prop_air_vel = sqrt(
            2 * _prop_thrust / (telem_data["AirDensity"] * (math.pi * (self.prop_diameter / 2) ** 2)) + _airspeed**2
        )

        telem_data["_prop_thrust"] = _prop_thrust

        if abs(incidence_vec.y) > 0.5 or _prop_air_vel > 1:  # avoid edge cases
            _elevator_aoa = atan2(-incidence_vec.y, _prop_air_vel) * deg
        else:
            _elevator_aoa = 0
        telem_data["_elevator_aoa"] = _elevator_aoa

        # calculate dynamic pressure based on air flow from propeller
        # elevator_prop_flow_ratio defines how much prop wash the elevator receives
        _elev_dyn_pressure = (
            utils.mix(
                telem_data["DynPressure"],
                0.5 * telem_data["AirDensity"] * _prop_air_vel**2,
                self.elevator_prop_flow_ratio,
            )
            * self.__dyn_pressure_scale
        )

        # scale dynamic pressure to FFB friendly values
        _dyn_pressure = telem_data["DynPressure"] * self.__dyn_pressure_scale

        # determine standard Q with Vne to get proper gain

        if telem_data["src"] == "XPLANE":
            vne = telem_data.get("Vne")
            vs0 = telem_data.get("Vso")
        else:
            vc, vs0, vs1 = telem_data.get("DesignSpeed")  # m/s   Vc is TAS!!
            telem_data["Vc_kt"] = vc * ms2kt
            Tvne = vc * 1.4  # rough estimate that Vne is 1.4x Vc
            # correction from TAS to IAS
            # https://aviation.stackexchange.com/questions/25801/how-do-you-convert-true-airspeed-to-indicated-airspeed
            qv = 0.5 * std_air_pressure * (Tvne**2)
            kmNs = ((qv / P0) + 1) ** (2 / 7)
            vne = vsound * sqrt(5 * (kmNs - 1))
        telem_data["Vne_ms_calc"] = vne

        if self.vne_override:
            vne = self.vne_override

        telem_data["Vne_kt"] = vne * ms2kt

        Qvne = 0.5 * std_air_pressure * vne**2
        # Qvc = 0.5 * std_air_pressure * (vne/1.4) ** 2
        telem_data["Qvne"] = Qvne * self.__dyn_pressure_scale

        Q_gain = 1 / (Qvne * self.__dyn_pressure_scale)
        telem_data["Qvc_gain"] = Q_gain

        self.elevator_gain = Q_gain
        self.aileron_gain = Q_gain
        self.rudder_gain = Q_gain

        _slip_gain = 1.0 - self.slip_gain * abs(sin(slip_angle))
        telem_data["_slip_gain"] = _slip_gain

        # increasing G force causes increase in elevator droop effect
        _elevator_droop_term = self.elevator_droop_moment * g_force / (1 + _elev_dyn_pressure)
        telem_data["_elevator_droop_term"] = _elevator_droop_term
        # logging.debug(f"ailer gain = {self.aileron_gain}")
        aileron_coeff = _dyn_pressure * self.aileron_gain * _slip_gain

        # add data to telemetry packet so they become visible in the GUI output
        telem_data["_prop_air_vel"] = _prop_air_vel
        telem_data["_elev_dyn_pressure"] = _elev_dyn_pressure
        # logging.debug(f"elev gain = {self.elevator_gain}")
        elevator_coeff = (_elev_dyn_pressure) * self.elevator_gain * _slip_gain
        # a, b, c = 0.5, 0.3, 0.1
        # elevator_coeff = a * (_elev_dyn_pressure ** 2) + b * _elev_dyn_pressure * self.elevator_gain + c * slip_gain

        # apply expo curve
        if self.spring_mode_is(SpringModeEnum.ADVANCED):
            # calculate spd based on current elevator_coeff assuming linear from 0 to VNE
            adv_spr_stgs = json.loads(self.adv_spr_gains)
            scale = adv_spr_stgs.get("scale")
            spd_y = scale * elevator_coeff
            spd_x = scale * aileron_coeff
            y_gains = utils.get_gain_from_speed(self.adv_spr_gains, spd_y)
            x_gains = utils.get_gain_from_speed(self.adv_spr_gains, spd_x)
            elevator_coeff = y_gains.get("y")
            aileron_coeff = x_gains.get("x")
        else:
            elevator_coeff = utils.expocurve(elevator_coeff, self.elevator_expo)
            aileron_coeff = utils.expocurve(aileron_coeff, self.aileron_expo)

        telem_data["_elev_coeff"] = elevator_coeff
        telem_data["_aile_coeff"] = aileron_coeff

        # force is proportional to elevator deflection vs incoming airflow, this creates a dynamic elevator effect on top of spring
        # update: reworking this based on spring center point offset and this below is not physically correct anyways
        # _aoa_term = sin(( _aoa - telem_data["ElevDefl"]) * rad) * self.aoa_gain * _elev_dyn_pressure * _slip_gain
        # telem_data["_aoa_term"] = _aoa_term
        input_data = HapticEffect.device.get_input()
        dx, dy = input_data.CP_scaled_axisXY()
        _G_term = self.g_force_gain * telem_data["AccBody"][1]
        _G_term = _G_term * abs(dy)  # scale g forces based on stick deflection from spring center
        telem_data["_G_term"] = _G_term

        #       hpf_pitch_acc = hpf.get("xacc", 3).update(data["RelWndY"]) # test stuff
        #       data["_hpf_pitch_acc"] = hpf_pitch_acc # test stuff
        _rud_dyn_pressure = (
            utils.mix(
                telem_data["DynPressure"],
                0.5 * telem_data["AirDensity"] * _prop_air_vel**2,
                self.rudder_prop_flow_ratio,
            )
            * self.__dyn_pressure_scale
        )
        rudder_coeff = _rud_dyn_pressure * self.rudder_gain * _slip_gain

        # apply expo curve
        if self.spring_mode_is(SpringModeEnum.ADVANCED):
            # calculate spd based on current elevator_coeff assuming linear from 0 to VNE
            adv_spr_stgs = json.loads(self.adv_spr_gains)
            scale = adv_spr_stgs.get("scale")
            spd_x = scale * rudder_coeff
            x_gains = utils.get_gain_from_speed(self.adv_spr_gains, spd_x)
            rudder_coeff = x_gains.get("x")
        else:
            rudder_coeff = utils.expocurve(rudder_coeff, self.rudder_expo)

        telem_data["_rud_coeff"] = rudder_coeff
        rud = (slip_angle - rudder_angle) * _dyn_pressure * _slip_gain
        rud_force = clamp((rud * self.rudder_gain), -1, 1)
        rud_force = self.dampener.dampen_value(rud_force, "_rud_force", derivative_hz=5, derivative_k=0.015)

        if ffb_type == "joystick":

            if self.trim_following and self.telemffb_controls_axes and not self.local_disable_axis_control:

                elev_trim = telem_data.get("ElevTrimPct", 0)
                aileron_trim = telem_data.get("AileronTrimPct", 0)

                aileron_trim = clamp(aileron_trim * self.joystick_trim_follow_gain_physical_x, -1, 1)
                virtual_stick_x_offs = aileron_trim - (aileron_trim * self.joystick_trim_follow_gain_virtual_x)

                elev_trim = clamp(elev_trim * self.joystick_trim_follow_gain_physical_y, -1, 1)

                elev_trim = self.dampener.dampen_value(elev_trim, "_elev_trim", derivative_hz=5, derivative_k=0.15)

                virtual_stick_y_offs = elev_trim - (elev_trim * self.joystick_trim_follow_gain_virtual_y)
                phys_stick_y_offs = int(elev_trim * 4096)

                if self.ap_following and ap_active:
                    if self._sim_is_msfs():
                        aileron_pos = telem_data.get("AileronDeflPctLR", (0, 0))
                        aileron_pos = aileron_pos[0]
                        aileron_pos = self.dampener.dampen_value(
                            aileron_pos, "_aileron_pos", derivative_hz=5, derivative_k=0.15
                        )
                    elif self._sim_is_xplane():
                        aileron_pos = telem_data.get("APRollServo", 0)
                    else:
                        aileron_pos = 0

                    phys_stick_x_offs = int(aileron_pos * 4096)
                else:
                    phys_stick_x_offs = int(aileron_trim * 4096)
            else:
                phys_stick_x_offs = 0
                virtual_stick_x_offs = 0
                phys_stick_y_offs = 0
                virtual_stick_y_offs = 0

            if self.telemffb_controls_axes and not self.local_disable_axis_control:
                input_data = HapticEffect.device.get_input()
                phys_x, phys_y = input_data.axisXY()
                telem_data["phys_x"] = phys_x
                telem_data["phys_y"] = phys_y

                x_pos = phys_x - virtual_stick_x_offs
                y_pos = phys_y - virtual_stick_y_offs

                x_scale = clamp(self.joystick_x_axis_scale, 0, 1)
                y_scale = clamp(self.joystick_y_axis_scale, 0, 1)
                if self._sim_is_xplane():
                    pos_x_pos = x_pos * x_scale
                    pos_y_pos = y_pos * y_scale
                    self.send_xp_command(f"AXIS:jx={round(pos_x_pos, 5)},jy={round(pos_y_pos, 5)}")

                if self._sim_is_msfs():
                    if self.enable_custom_x_axis:
                        x_var = self.custom_x_axis
                        x_range = self.raw_x_axis_scale
                    else:
                        x_var = "AXIS_AILERONS_SET"
                        x_range = 16384
                    if self.enable_custom_y_axis:
                        y_var = self.custom_y_axis
                        y_range = self.raw_y_axis_scale
                    else:
                        y_var = "AXIS_ELEVATOR_SET"
                        y_range = 16384

                    pos_x_pos = utils.scale(x_pos, (-1, 1), (-x_range * x_scale, x_range * x_scale))
                    pos_y_pos = utils.scale(y_pos, (-1, 1), (-y_range * y_scale, y_range * y_scale))

                    if x_range != 1:
                        pos_x_pos = -int(pos_x_pos)
                    else:
                        pos_x_pos = round(pos_x_pos, 5)
                    if y_range != 1:
                        pos_y_pos = -int(pos_y_pos)
                    else:
                        pos_y_pos = round(pos_y_pos, 5)

                    self._simconnect.send_event_to_msfs(x_var, pos_x_pos)
                    self._simconnect.send_event_to_msfs(y_var, pos_y_pos)

                # give option to disable if desired by user
            if (
                self.aoa_effect_enabled
                and telem_data.get("ElevDeflPct", 0) != 0
                and not max(telem_data.get("WeightOnWheels"))
            ):
                # calculate maximum angle based on current angle and percentage
                tot = telem_data["ElevDefl"] / telem_data["ElevDeflPct"]

                speed_factor = utils.scale_clamp(IAS, (0, vne), (0.0, 1.0))
                y_offs = _aoa / tot
                y_offs = y_offs + force_trim_y_offset + (phys_stick_y_offs / 4096)
                y_offs = clamp(y_offs, -1, 1)
                # Take speed in relation to aircraft v speeds into account when moving offset based on aoa
                y_offs = int(y_offs * 4096 * speed_factor * self.aoa_effect_gain)
            else:
                y_offs = force_trim_y_offset + (phys_stick_y_offs / 4096)
                y_offs = clamp(y_offs, -1, 1)
                y_offs = int(y_offs * 4096)

            self.spring_y.cpOffset = y_offs
            x_offs = phys_stick_x_offs
            self.spring_x.cpOffset = x_offs

            # logging.debug(f"fto={force_trim_y_offset} | Offset={offs}")
            # if self.adv_spr_override_enabled:
            #     if self.adv_spr_gains == 'none':
            #         self.flag_error('Please open and configure the advanced spring gain settings')
            #     else:
            #         gains = utils.get_gain_from_speed(self.adv_spr_gains, telem_data.get('IAS', 0))
            #         self.spring_y.positiveCoefficient = self.spring_y.negativeCoefficient = round(4096 * gains.get('y', 0))
            #         self.spring_x.positiveCoefficient = self.spring_x.negativeCoefficient = round(4096 * gains.get('x', 0))
            # else:
            max_coeff_y = int(4096 * self.max_elevator_coeff)
            realtime_coeff_y = int(4096 * elevator_coeff)
            ec = int(utils.scale_clamp(realtime_coeff_y, (base_elev_coeff, 4096), (base_elev_coeff, max_coeff_y)))

            pct_max_e = ec / max_coeff_y

            telem_data["_pct_max_e"] = pct_max_e
            self._ipc_telem["_pct_max_e"] = pct_max_e
            logging.debug(f"Elev Coef: {ec}")
            telem_data["_ec"] = ec

            self.spring_y.set_coefficient(ec)

            max_coeff_x = int(4096 * self.max_aileron_coeff)
            realtime_coeff_x = int(4096 * aileron_coeff)
            ac = int(utils.scale_clamp(realtime_coeff_x, (base_ailer_coeff, 4096), (base_ailer_coeff, max_coeff_x)))

            pct_max_a = ac / max_coeff_x

            telem_data["_pct_max_a"] = pct_max_a
            self._ipc_telem["_pct_max_a"] = pct_max_a
            telem_data["_ac"] = ac
            logging.debug(f"Ailer Coef: {ac}")

            self.spring_x.set_coefficient(ac)

            # update spring data
            self._spring_handle.setCondition(self.spring_y)
            self._spring_handle.setCondition(self.spring_x)

            # update constant forces
            cf_pitch = -_elevator_droop_term - _G_term  # + _aoa_term
            cf_pitch = clamp(cf_pitch, -1.0, 1.0)

            # add force on lateral axis (sideways)
            if self.uncoordinated_turn_effect_enabled:
                _side_accel = -telem_data["AccBody"][0] * self.lateral_force_gain
            else:
                _side_accel = 0

            cf_roll = _side_accel

            cf = Vector2D(cf_pitch, cf_roll)
            if cf.magnitude() > 1.0:
                cf = cf.normalize()

            mag, theta = cf.to_polar()

            self.effects["control_weight"].constant(mag, theta * deg).start()
            # print(mag, theta*deg)
            # self.const_force.constant(mag, theta*deg).start()

            self._spring_handle.start()  # ensure spring is started

        elif ffb_type == "pedals":
            if self.trim_following and self.telemffb_controls_axes and not self.local_disable_axis_control:

                rudder_trim = telem_data.get("RudderTrimPct", 0)

                rudder_trim = clamp(rudder_trim * self.rudder_trim_follow_gain_physical_x, -1, 1)
                virtual_rudder_x_offs = rudder_trim - (rudder_trim * self.rudder_trim_follow_gain_virtual_x)

                phys_rudder_x_offs = int(rudder_trim * 4096)
            else:
                phys_rudder_x_offs = 0
                virtual_rudder_x_offs = 0

            if self.spring_mode_is(SpringModeEnum.ADVANCED):
                if self.adv_spr_gains == "none":
                    self.flag_error("Please open and configure the advanced spring gain settings")
                else:
                    gains = utils.get_gain_from_speed(self.adv_spr_gains, telem_data.get("IAS", 0))
                    # print(f"gains: {gains}")
                    self.spring_x.set_coefficient(gains.get("x", 0))
                    rc = gains.get("x", 0)
            else:
                max_coeff_x = int(4096 * self.max_rudder_coeff)
                realtime_coeff_x = int(4096 * rudder_coeff)
                rc = int(
                    utils.scale_clamp(realtime_coeff_x, (base_rudder_coeff, 4096), (base_rudder_coeff, max_coeff_x))
                )

                pct_max_r = rc / max_coeff_x

                telem_data["_pct_max_r"] = pct_max_r
                self._ipc_telem["_pct_max_r"] = pct_max_r
                telem_data["_rc"] = rc
                self.spring_x.set_coefficient(rc)

            self.spring_x.cpOffset = phys_rudder_x_offs

            # add spring force from steering wheel on ground
            # should this be dependent on friction?  doesn't need to be.

            if self.steering_friction:
                on_ground = telem_data.get("SimOnGround", 0)
                wos = telem_data.get("WeightOnWheels", [0])[0]  # center steering wheel only
                gs = telem_data.get("GroundSpeed", 0)
                csa = telem_data.get("CenterSteerAnglePct", 0)
                wr = telem_data.get("WaterRudderExt", 0)  # percent of rudder extension
                surface = telem_data.get("SurfaceType", 0)

                if on_ground and (wos or surface == "Water"):
                    rudder_angle = 30  # assumed rudder travel
                    dynamic_angle = phys_rudder_x_offs * rudder_angle / 4096
                    dynamic_force = rc / 4096
                    steer_angle = csa * rudder_angle
                    steer_force = self.steering_friction_spring / 40  # dont need a strong spring
                    if surface == "Water":
                        steer_force *= wr
                    result_angle_percent, result_mag = utils.add_vectors_deg(
                        dynamic_angle, dynamic_force, steer_angle, steer_force
                    )

                    # logging.info(f"angle {result_angle_percent:.3f} mag {result_mag:.1f}  ofs {phys_rudder_x_offs/136:.1f}  rc {rc}  st angle {steer_angle:.1f} ")
                    self.spring_x.set_coefficient(result_mag, True)
                    self.spring_x.set_offset(result_angle_percent / rudder_angle)

            self._spring_handle.setCondition(self.spring_x)

            speed_factor = utils.scale_clamp(IAS, (0, vne), (0.0, 1.0))
            rud_force = rud_force * speed_factor
            # telem_data["RudForce"] = rud_force * speed_factor

            if self.telemffb_controls_axes and not self.local_disable_axis_control:
                input_data = HapticEffect.device.get_input()
                phys_x, phys_y = input_data.axisXY()
                telem_data["phys_x"] = phys_x
                x_pos = phys_x - virtual_rudder_x_offs
                x_scale = clamp(self.rudder_x_axis_scale, 0, 1)

                if self._sim_is_xplane():
                    pos_x_pos = x_pos * x_scale
                    self.send_xp_command(f"AXIS:px={round(pos_x_pos, 5)}")

                if self._sim_is_msfs():
                    if self.enable_custom_x_axis:
                        x_var = self.custom_x_axis
                        x_range = self.raw_x_axis_scale
                    else:
                        x_var = "AXIS_RUDDER_SET"
                        x_range = 16384

                    pos_x_pos = utils.scale(x_pos, (-1, 1), (-x_range * x_scale, x_range * x_scale))

                    if x_range != 1:
                        pos_x_pos = -int(pos_x_pos)
                    else:
                        pos_x_pos = round(pos_x_pos, 5)

                    self._simconnect.send_event_to_msfs(x_var, pos_x_pos)

            self.const_force.constant(rud_force, 270).start()
            self._spring_handle.start()
