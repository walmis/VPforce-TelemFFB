# 
# This file is part of the TelemFFB distribution (https://github.com/walmis/TelemFFB).
# Copyright (c) 2023 Valmantas Palikša.
# 
# This program is free software: you can redistribute it and/or modify  
# it under the terms of the GNU General Public License as published by  
# the Free Software Foundation, version 3.
#
# This program is distributed in the hope that it will be useful, but 
# WITHOUT ANY WARRANTY; without even the implied warranty of 
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU 
# General Public License for more details.
#
# You should have received a copy of the GNU General Public License 
# along with this program. If not, see <http://www.gnu.org/licenses/>.
#

import time
import math
from math import sin, cos, radians, sqrt, atan2
from ctypes import byref, cast, sizeof
from time import sleep
from pprint import pprint
import sys
import logging
import utils
from typing import List, Dict
from utils import clamp, HighPassFilter, Derivative, Dispenser

from ffb_rhino import HapticEffect, FFBReport_SetCondition, FFBReport_Input
from aircraft_base import AircraftBase, effects, HPFs, LPFs



deg = 180 / math.pi
slugft3 = 0.00194032  # SI to slugft3
rad = 0.0174532925
ft = 3.28084  # m to ft
kt = 1.94384  # ms to kt
kt2ms = 0.514444  # knots to m/s


class Aircraft(AircraftBase):
    """Base class for Aircraft based FFB"""

    buffeting_intensity: float = 0.2  # peak AoA buffeting intensity  0 to disable
    buffet_aoa: float = 10.0  # AoA when buffeting starts
    stall_aoa: float = 15.0  # Stall AoA
    aoa_effect_enabled: int = 1

    aoa_buffeting_enabled: bool = True
    aoa_effect_gain: float = 1.0
    uncoordinated_turn_effect_enabled: int = 1

    runway_rumble_intensity: float = 1.0  # peak runway intensity, 0 to disable
    runway_rumble_enabled: bool = True
    gun_vibration_intensity: float = 0.12  # peak gunfire vibration intensity, 0 to disable
    cm_vibration_intensity: float = 0.12  # peak countermeasure release vibration intensity, 0 to disable
    weapon_release_intensity: float = 0.12  # peak weapon release vibration intensity, 0 to disable
    weapon_effect_direction: int = 45  # Affects the direction of force applied for gun/cm/weapon release effect, Set to -1 for random direction

    speedbrake_motion_intensity: float = 0.12  # peak vibration intensity when speed brake is moving, 0 to disable
    speedbrake_buffet_intensity: float = 0.15  # peak buffeting intensity when speed brake deployed,  0 to disable

    spoiler_motion_intensity: float = 0.0  # peak vibration intensity when spoilers is moving, 0 to disable
    spoiler_buffet_intensity: float = 0.15  # peak buffeting intensity when spoilers deployed,  0 to disable

    gear_motion_effect_enabled: bool = True
    gear_motion_intensity: float = 0.12
    gear_buffet_effect_enabled: bool = True
    gear_buffet_intensity: float = 0.15     # peak buffeting intensity when gear down during flight,  0 to disable

    flaps_motion_intensity: float = 0.12  # peak vibration intensity when flaps are moving, 0 to disable
    flaps_buffet_intensity: float = 0.0  # peak buffeting intensity when flaps are deployed,  0 to disable

    canopy_motion_intensity: float = 0.12  # peak vibration intensity when canopy is moving, 0 to disable
    canopy_buffet_intensity: float = 0.0  # peak buffeting intensity when canopy is open during flight,  0 to disable

    afterburner_effect_intensity = 0.2  # peak intensity for afterburner rumble effect
    jet_engine_rumble_intensity = 0.12  # peak intensity for jet engine rumble effect
    jet_engine_rumble_freq = 45  # base frequency for jet engine rumble effect (Hz)
    ####
    #### Beta effects - set to 1 to enable
    deceleration_effect_enable = 0
    deceleration_effect_enable_areyoureallysure = 0
    deceleration_max_force = 0.5
    ###

    ####
    #### Beta effects - set to 1 to enable
    gforce_effect_invert_force = 0  # 0=disabled(default),1=enabled (case where "180" degrees does not equal "away from pilot")
    gforce_effect_enable = 0
    gforce_effect_enable_areyoureallysure = 0
    gforce_effect_curvature = 2.2
    gforce_effect_max_intensity = 1.0
    gforce_min_gs = 1.5  # G's where the effect starts playing
    gforce_max_gs = 5.0  # G limit where the effect maxes out at strength defined in gforce_effect_max_intensity

    ###
    ### AoA reduction force effect
    ###
    aoa_reduction_effect_enabled = 0
    aoa_reduction_max_force = 0.0
    critical_aoa_start = 22
    critical_aoa_max = 25

    rotor_blade_count = 2
    heli_engine_rumble_intensity=0.15

    aircraft_is_spring_centered = 0
    spring_centered_elev_gain = 0.5
    spring_centered_ailer_gain = 0.5
    aileron_spring_gain = 0.25
    elevator_spring_gain = 0.25
    rudder_spring_gain = 0.25

    aircraft_is_fbw = 0
    fbw_elevator_gain = 0.8
    fbw_aileron_gain = 0.8
    fbw_rudder_gain = 0.8

    nosewheel_shimmy = 0
    nosewheel_shimmy_intensity = 0.15
    nosewheel_shimmy_min_speed = 7
    nosewheel_shimmy_min_brakes = 0.6

    force_trim_enabled = 0
    cyclic_spring_gain = 1.0
    force_trim_button = 0
    force_trim_reset_button = 0
    include_dynamic_stick_forces = True

    elevator_force_trim = 0
    aileron_force_trim = 0

    smoother = utils.Smoother()
    dampener = utils.Derivative()
    center_spring_on_pause = False

    use_legacy_bindings = False
    enable_custom_x_axis = False
    enable_custom_y_axis = False
    custom_x_axis: str = ''
    custom_y_axis: str = ''
    raw_x_axis_scale: int = 16384
    raw_y_axis_scale: int = 16384

    @classmethod
    def set_simconnect(cls, sc):
        cls._simconnect = sc
        
    def __init__(self, name, **kwargs) -> None:
        super().__init__(name)
        # clear any existing effects
        for e in effects.values(): e.destroy()
        effects.clear()
        # self.spring = HapticEffect().spring()
        self.spring = effects["spring"].spring()
        self.pause_spring = effects["pause_spring"].spring()
        self.spring_x = FFBReport_SetCondition(parameterBlockOffset=0)
        self.spring_y = FFBReport_SetCondition(parameterBlockOffset=1)
        self.const_force = HapticEffect().constant(0, 0)

        # aileron_max_deflection = 20.0*0.01745329
        self.elevator_max_deflection = 12.0 * 0.01745329
        # rudder_max_deflection = 20.0*0.01745329
        self.aileron_gain = 0.1
        self.elevator_gain = 0.1
        self.rudder_gain = 0.1
        self.slip_gain = 1.0
        self.stall_AoA = 18.0 * 0.01745329
        self.pusher_start_AoA = 900.0 * 0.01745329
        self.pusher_working_angle = 900.0 * 0.01745329
        self.wing_shadow_AoA = 900.0 * 0.01745329
        self.wing_shadow_angle = 900.0 * 0.01745329
        self.stick_shaker_AoA = 16.0 * 0.01745329

        # FFB force value per lateral G
        self.lateral_force_gain = 0.2 

        self.aoa_gain = 0.3
        self.g_force_gain = 0.1
        self.prop_diameter = 1.5

        self.elevator_droop_moment = 0.1  # in FFB force units

        # how much air flow the elevator receives from the propeller
        self.elevator_prop_flow_ratio = 1.0
        self.rudder_prop_flow_ratio = 1.0

        # scale the dynamic pressure to ffb friendly values
        self.dyn_pressure_scale = 0.005
        self.max_aoa_cf_force: float = 0.2  # CF force sent to device at %stall_aoa

        self.cyclic_trim_release_active = 0
        self.cyclic_spring_init = 0
        self.cyclic_center = [0, 0]  # x, y
        self.collective_spring_init = 0

        self.force_trim_release_active = 0
        self.force_trim_spring_init = 0
        self.stick_center = [0, 0]  # x, y

        self.force_trim_x_offset = 0
        self.force_trim_y_offset = 0

        self.telemffb_controls_axes = False

        self.trim_following = False
        self.joystick_x_axis_scale = 1.0
        self.joystick_y_axis_scale = 1.0
        self.rudder_x_axis_scale = 1.0

        self.joystick_trim_follow_gain_physical_x = 1.0
        self.joystick_trim_follow_gain_physical_y = 0.2
        self.joystick_trim_follow_gain_virtual_x = 1.0
        self.joystick_trim_follow_gain_virtual_y = 0.2
        self.rudder_trim_follow_gain_physical_x = 1.0
        self.rudder_trim_follow_gain_virtual_x = 0.2



        self.ap_following = True

        self.use_fbw_for_ap_follow = True

        self.invert_ap_x_axis = False
        self.max_elevator_coeff = 0.5
        self.max_aileron_coeff = 0.5
        self.max_rudder_coeff = 0.5

    def _update_nosewheel_shimmy(self, telem_data):
        curve = 2.5
        freq = 8
        brakes = telem_data.get("Brakes", (0, 0))
        wow = sum(telem_data.get("WeightOnWheels", 0))
        tas = telem_data.get("TAS", 0)
        logging.debug(f"brakes = {brakes}")
        avg_brakes = sum(brakes) / len(brakes)
        if avg_brakes >= self.nosewheel_shimmy_min_brakes and tas > self.nosewheel_shimmy_min_speed:
            shimmy = utils.non_linear_scaling(avg_brakes, self.nosewheel_shimmy_min_brakes, 1.0, curvature=curve)
            logging.debug(f"Nosewheel Shimmy intensity calculation: (BrakesPct:{avg_brakes} | TAS:{tas} | RT Inensity: {shimmy}")
            effects["nw_shimmy"].periodic(freq, shimmy, 90).start()
        else:
            effects.dispose("nw_shimmy")

        self.cp_int = 0

    def _update_fbw_flight_controls(self, telem_data):
        ffb_type = telem_data.get("FFBType", "joystick")
        ap_active = telem_data.get("APMaster", 0)
        self.spring = effects['fbw_spring'].spring()
        if ffb_type == "joystick":

            if self.trim_following:
                if not self.telemffb_controls_axes:
                    logging.warning("TRIM FOLLOWING ENABLED BUT TELEMFFB IS NOT CONFIGURED TO SEND AXIS POSITION TO MSFS! Forcing to enable!")
                    self.telemffb_controls_axes = True      # Force sending of axis via simconnect if trim following is enabled
                elev_trim = telem_data.get("ElevTrimPct", 0)

                # derivative_hz = 5  # derivative lpf filter -3db Hz
                # derivative_k = 0.1  # derivative gain value, or damping ratio
                #
                # d_elev_trim = getattr(self, "_d_elev_trim", None)
                # if not d_elev_trim: d_elev_trim = self._d_elev_trim = utils.Derivative(derivative_hz)
                # d_elev_trim.lpf.cutoff_freq_hz = derivative_hz
                #
                # elev_trim_deriv = - d_elev_trim.update(elev_trim) * derivative_k
                #
                # elev_trim += elev_trim_deriv
                elev_trim = self.dampener.dampen_value(elev_trim, '_elev_trim', derivative_hz=5, derivative_k=0.15)

                # print(f"raw:{raw_elev_trim}, smooth:{elev_trim}")
                aileron_trim = telem_data.get("AileronTrimPct", 0)

                aileron_trim = clamp(aileron_trim * self.joystick_trim_follow_gain_physical_x, -1, 1)
                virtual_stick_x_offs = aileron_trim - (aileron_trim * self.joystick_trim_follow_gain_virtual_x)

                elev_trim = clamp(elev_trim * self.joystick_trim_follow_gain_physical_y, -1, 1)
                virtual_stick_y_offs = elev_trim - (elev_trim * self.joystick_trim_follow_gain_virtual_y)

                phys_stick_y_offs = int(elev_trim*4096)

                if self.ap_following and ap_active:
                    input_data = HapticEffect.device.getInput()
                    phys_x, phys_y = input_data.axisXY()
                    aileron_pos = telem_data.get("AileronDeflPctLR", (0, 0))
                    elevator_pos = telem_data.get("ElevDeflPct", 0)
                    aileron_pos = aileron_pos[0]

                    aileron_pos = self.dampener.dampen_value(aileron_pos, '_aileron_pos', derivative_hz=5, derivative_k=0.15)
                    # derivative_hz = 5  # derivative lpf filter -3db Hz
                    # derivative_k = 0.1  # derivative gain value, or damping ratio
                    #
                    # d_aileron_pos = getattr(self, "_d_aileron_pos", None)
                    # if not d_aileron_pos: d_aileron_pos = self._d_aileron_pos = utils.Derivative(derivative_hz)
                    # d_aileron_pos.lpf.cutoff_freq_hz = derivative_hz
                    #
                    # aileron_pos_deriv = - d_aileron_pos.update(aileron_pos) * derivative_k
                    #
                    # aileron_pos += aileron_pos_deriv


                    phys_stick_x_offs = int(aileron_pos * 4096)
                    if self.invert_ap_x_axis:
                        phys_stick_x_offs = -phys_stick_x_offs
                else:
                    phys_stick_x_offs = int(aileron_trim * 4096)

            else:
                phys_stick_x_offs = 0
                virtual_stick_x_offs = 0
                phys_stick_y_offs = 0
                virtual_stick_y_offs = 0

            if self.telemffb_controls_axes:
                input_data = HapticEffect.device.getInput()
                phys_x, phys_y = input_data.axisXY()
                telem_data['phys_x'] = phys_x
                telem_data['phys_y'] = phys_y
                x_pos = phys_x - virtual_stick_x_offs
                y_pos = phys_y - virtual_stick_y_offs

                x_scale = clamp(self.joystick_x_axis_scale, 0, 1)
                y_scale = clamp(self.joystick_y_axis_scale, 0, 1)

                if self.enable_custom_x_axis:
                    x_var = self.custom_x_axis
                    x_range = self.raw_x_axis_scale
                else:
                    x_var = 'AXIS_AILERONS_SET'
                    x_range = 16384
                if self.enable_custom_y_axis:
                    y_var = self.custom_y_axis
                    y_range = self.raw_y_axis_scale
                else:
                    y_var = 'AXIS_ELEVATOR_SET'
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
            # update spring data
            if self.ap_following and ap_active:
                y_coeff = 4096
                x_coeff = 4096
            else:
                y_coeff = clamp(int(4096 * self.fbw_elevator_gain), 0, 4096)
                x_coeff = clamp(int(4096 * self.fbw_aileron_gain), 0, 4096)

            self.spring_y.negativeCoefficient = self.spring_y.positiveCoefficient = y_coeff
            # logging.debug(f"Elev Coeef: {elevator_coeff}")
            self.spring_y.cpOffset = phys_stick_y_offs

            self.spring_x.negativeCoefficient = self.spring_x.positiveCoefficient = x_coeff
            self.spring_x.cpOffset = phys_stick_x_offs

            self.spring.effect.setCondition(self.spring_y)
            self.spring.effect.setCondition(self.spring_x)

        elif ffb_type == "pedals":
            if self.trim_following:
                if not self.telemffb_controls_axes:
                    logging.warning("TRIM FOLLOWING ENABLED BUT TELEMFFB IS NOT CONFIGURED TO SEND AXIS POSITION TO MSFS! Forcing to enable!")
                    self.telemffb_controls_axes = True      # Force sending of axis via simconnect if trim following is enabled
                rudder_trim = telem_data.get("RudderTrimPct", 0)

                rudder_trim = clamp(rudder_trim * self.rudder_trim_follow_gain_physical_x, -1, 1)
                virtual_rudder_x_offs = rudder_trim - (rudder_trim * self.rudder_trim_follow_gain_virtual_x)

                phys_rudder_x_offs = int(rudder_trim * 4096)

                if self.ap_following and ap_active:
                    input_data = HapticEffect.device.getInput()
                    # print("I am here")
                    phys_x, phys_y = input_data.axisXY()
                    rudder_pos = telem_data.get("RudderDeflPct", 0)
                    rudder_pos = self.dampener.dampen_value(rudder_pos, '_rudder_pos', derivative_hz=5, derivative_k=0.15)
                    # derivative_hz = 5  # derivative lpf filter -3db Hz
                    # derivative_k = 0.1  # derivative gain value, or damping ratio
                    #
                    # d_rudder_pos = getattr(self, "_d_rudder_pos", None)
                    # if not d_rudder_pos: d_rudder_pos = self._d_rudder_pos = utils.Derivative(derivative_hz)
                    # d_rudder_pos.lpf.cutoff_freq_hz = derivative_hz
                    #
                    # rudder_pos_deriv = - d_rudder_pos.update(rudder_pos) * derivative_k
                    #
                    # rudder_pos += rudder_pos_deriv


                    phys_rudder_x_offs = int(rudder_pos * 4096)

                else:
                    phys_rudder_x_offs = int(rudder_trim * 4096)

            else:
                phys_rudder_x_offs = 0
                virtual_rudder_x_offs = 0

            if self.telemffb_controls_axes:
                input_data = HapticEffect.device.getInput()
                phys_x, phys_y = input_data.axisXY()
                telem_data['phys_x'] = phys_x

                x_pos = phys_x - virtual_rudder_x_offs
                x_scale = clamp(self.rudder_x_axis_scale, 0, 1)

                if self.enable_custom_x_axis:
                    x_var = self.custom_x_axis
                    x_range = self.raw_x_axis_scale
                else:
                    x_var = 'AXIS_RUDDER_SET'
                    x_range = 16384

                pos_x_pos = utils.scale(x_pos, (-1, 1), (-x_range * x_scale, x_range * x_scale))

                if x_range != 1:
                    pos_x_pos = -int(pos_x_pos)
                else:
                    pos_x_pos = round(pos_x_pos, 5)

                self._simconnect.send_event_to_msfs(x_var, pos_x_pos)

                # update spring data

            if self.ap_following and ap_active:
                x_coeff = 4096
            else:
                x_coeff = clamp(int(4096 * self.fbw_rudder_gain), 0, 4096)

            self.spring_x.negativeCoefficient = self.spring_x.positiveCoefficient = x_coeff
            # print(f"{phys_rudder_x_offs}")
            self.spring_x.cpOffset = phys_rudder_x_offs
            logging.debug(f"Elev Coeef: {x_coeff}")

            self.spring.effect.setCondition(self.spring_x)
        self.spring.start()

    def _update_flight_controls(self, telem_data):
        # calculations loosely based on FLightGear FFB page:
        # https://wiki.flightgear.org/Force_feedback
        # https://github.com/viktorradnai/fg-haptic/blob/master/force-feedback.nas
        self.spring = effects["dynamic_spring"].spring()
        ap_active = telem_data.get("APMaster", 0)

        elev_base_gain = 0
        ailer_base_gain = 0
        rudder_base_gain = 0
        ffb_type = telem_data.get("FFBType", "joystick")
        if self.aircraft_is_fbw or telem_data.get("ACisFBW"):
            logging.debug ("FBW Setting enabled, running fbw_flight_controls")
            self._update_fbw_flight_controls(telem_data)
            return

        if telem_data.get("AircraftClass") == "Helicopter":
            logging.debug("Aircraft is Helicopter, aborting update_flight_controls")
            return

        if self.ap_following and ap_active and self.use_fbw_for_ap_follow:
            logging.debug("FBW Setting enabled, running fbw_flight_controls")
            self._update_fbw_flight_controls(telem_data)
            effects["dynamic_spring"].stop()
            return
        else:
            effects["fbw_spring"].stop()

        if self.aircraft_is_spring_centered:
            elev_base_gain = self.elevator_spring_gain
            ailer_base_gain = self.aileron_spring_gain
            rudder_base_gain = self.rudder_spring_gain
            logging.debug(f"Aircraft controls are center sprung, setting x:y base gain to{ailer_base_gain}:{elev_base_gain}, rudder base gain to {rudder_base_gain}")
        
        incidence_vec = utils.Vector(telem_data["VelWorld"])
        wind_vec = utils.Vector(telem_data["AmbWind"])
        incidence_vec = incidence_vec - wind_vec
        # Rotate the vector from world frame into body frame
        incidence_vec = incidence_vec.rotY(-(telem_data["Heading"] * rad))
        incidence_vec = incidence_vec.rotX(-telem_data["Pitch"] * rad)
        incidence_vec = incidence_vec.rotZ(-telem_data["Roll"] * rad)
        force_trim_x_offset = self.force_trim_x_offset
        force_trim_y_offset = self.force_trim_y_offset

        _airspeed = incidence_vec.z
        telem_data["TAS"] = _airspeed

        base_elev_coeff = round(clamp((elev_base_gain * 4096), 0, 4096))
        base_ailer_coeff = round(clamp((ailer_base_gain * 4096), 0, 4096))
        base_rudder_coeff = round(clamp((rudder_base_gain * 4096), 0, 4096))

        #logging.info(f"Base Elev/Ailer coeff = {base_elev_coeff}/{base_ailer_coeff}")

        rudder_angle = telem_data["RudderDefl"] * rad  # + trim?

        # print(data["ElevDefl"] / data["ElevDeflPct"] * 100)

        slip_angle = atan2(incidence_vec.x, incidence_vec.z)
        telem_data["SideSlip"] = slip_angle*deg # overwrite sideslip with our calculated version (including wind)
        g_force = telem_data["G"] # this includes earths gravity

        _aoa = -atan2(incidence_vec.y, incidence_vec.z)*deg
        telem_data["AoA"] = _aoa

        # calculate air flow velocity exiting the prop
        # based on https://www.grc.nasa.gov/www/k-12/airplane/propth.html
        _prop_thrust1 = telem_data.get('PropThrust1', 0)
        if _prop_thrust1 < 0:
            _prop_thrust1 = 0
        _prop_air_vel = sqrt(2 * _prop_thrust1 / (telem_data["AirDensity"] * (math.pi * (self.prop_diameter / 2) ** 2)) + _airspeed ** 2)

        if abs(incidence_vec.y) > 0.5 or _prop_air_vel > 1: # avoid edge cases
            _elevator_aoa = atan2(-incidence_vec.y, _prop_air_vel) * deg
        else:
            _elevator_aoa = 0
        telem_data["_elevator_aoa"] = _elevator_aoa
        telem_data["Incidence"] = [incidence_vec.x, incidence_vec.y, incidence_vec.z]

        # calculate dynamic pressure based on air flow from propeller
        # elevator_prop_flow_ratio defines how much prop wash the elevator receives
        _elev_dyn_pressure = utils.mix(telem_data["DynPressure"], 
                                       0.5 * telem_data["AirDensity"] * _prop_air_vel ** 2, 
                                       self.elevator_prop_flow_ratio) * self.dyn_pressure_scale

        # scale dynamic pressure to FFB friendly values
        _dyn_pressure = telem_data["DynPressure"] * self.dyn_pressure_scale

        _slip_gain = 1.0 - self.slip_gain * abs(sin(slip_angle))
        telem_data["_slip_gain"] = _slip_gain

        # increasing G force causes increase in elevator droop effect
        _elevator_droop_term = self.elevator_droop_moment * g_force / (1 + _elev_dyn_pressure)
        telem_data["_elevator_droop_term"] = _elevator_droop_term
        #logging.debug(f"ailer gain = {self.aileron_gain}")
        aileron_coeff = _dyn_pressure * self.aileron_gain * _slip_gain

        # add data to telemetry packet so they become visible in the GUI output
        telem_data["_prop_air_vel"] = _prop_air_vel
        telem_data["_elev_dyn_pressure"] = _elev_dyn_pressure
        #logging.debug(f"elev gain = {self.elevator_gain}")
        elevator_coeff = (_elev_dyn_pressure) * self.elevator_gain * _slip_gain
        # a, b, c = 0.5, 0.3, 0.1
        # elevator_coeff = a * (_elev_dyn_pressure ** 2) + b * _elev_dyn_pressure * self.elevator_gain + c * slip_gain

        telem_data["_elev_coeff"] = elevator_coeff
        telem_data["_aile_coeff"] = aileron_coeff

        # force is proportional to elevator deflection vs incoming airflow, this creates a dynamic elevator effect on top of spring
        # update: reworking this based on spring center point offset and this below is not physically correct anyways
        #_aoa_term = sin(( _aoa - telem_data["ElevDefl"]) * rad) * self.aoa_gain * _elev_dyn_pressure * _slip_gain
        #telem_data["_aoa_term"] = _aoa_term

        _G_term = (self.g_force_gain * telem_data["AccBody"][1])
        telem_data["_G_term"] = _G_term

        #       hpf_pitch_acc = hpf.get("xacc", 3).update(data["RelWndY"]) # test stuff
        #       data["_hpf_pitch_acc"] = hpf_pitch_acc # test stuff
        _rud_dyn_pressure = utils.mix(telem_data["DynPressure"], 0.5 * telem_data["AirDensity"] * _prop_air_vel ** 2,
                                      self.rudder_prop_flow_ratio) * self.dyn_pressure_scale
        rudder_coeff = _rud_dyn_pressure * self.rudder_gain * _slip_gain
        telem_data["_rud_coeff"] = rudder_coeff
        rud = (slip_angle - rudder_angle) * _dyn_pressure * _slip_gain
        rud_force = clamp((rud * self.rudder_gain), -1, 1)
        rud_force = self.dampener.dampen_value(rud_force, '_rud_force', derivative_hz=5, derivative_k=.015)

        if ffb_type == 'joystick':

            if self.trim_following:
                self.telemffb_controls_axes = True  # Force sending of axis via simconnect if trim following is enabled
                elev_trim = telem_data.get("ElevTrimPct", 0)
                aileron_trim = telem_data.get("AileronTrimPct", 0)

                aileron_trim = clamp(aileron_trim * self.joystick_trim_follow_gain_physical_x, -1, 1)
                virtual_stick_x_offs = aileron_trim - (aileron_trim * self.joystick_trim_follow_gain_virtual_x)

                elev_trim = clamp(elev_trim * self.joystick_trim_follow_gain_physical_y, -1, 1)

                elev_trim = self.dampener.dampen_value(elev_trim, '_elev_trim', derivative_hz=5, derivative_k=0.15)

                virtual_stick_y_offs = elev_trim - (elev_trim * self.joystick_trim_follow_gain_virtual_y)
                phys_stick_y_offs = int(elev_trim * 4096)


                if self.ap_following and ap_active:
                    aileron_pos = telem_data.get("AileronDeflPctLR", (0, 0))

                    aileron_pos = aileron_pos[0]
                    aileron_pos = self.dampener.dampen_value(aileron_pos, '_aileron_pos', derivative_hz=5, derivative_k=0.15)

                    phys_stick_x_offs = int(aileron_pos * 4096)
                else:
                    phys_stick_x_offs = int(aileron_trim * 4096)
            else:
                phys_stick_x_offs = 0
                virtual_stick_x_offs = 0
                phys_stick_y_offs = 0
                virtual_stick_y_offs = 0

            if self.telemffb_controls_axes:
                input_data = HapticEffect.device.getInput()
                phys_x, phys_y = input_data.axisXY()
                telem_data['phys_x'] = phys_x
                telem_data['phys_y'] = phys_y

                x_pos = phys_x - virtual_stick_x_offs
                y_pos = phys_y - virtual_stick_y_offs

                x_scale = clamp(self.joystick_x_axis_scale, 0, 1)
                y_scale = clamp(self.joystick_y_axis_scale, 0, 1)

                if self.enable_custom_x_axis:
                    x_var = self.custom_x_axis
                    x_range = self.raw_x_axis_scale
                else:
                    x_var = 'AXIS_AILERONS_SET'
                    x_range = 16384
                if self.enable_custom_y_axis:
                    y_var = self.custom_y_axis
                    y_range = self.raw_y_axis_scale
                else:
                    y_var = 'AXIS_ELEVATOR_SET'
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

                #give option to disable if desired by user
            if self.aoa_effect_enabled and telem_data["ElevDeflPct"] != 0 and not max(telem_data.get("WeightOnWheels")):
                # calculate maximum angle based on current angle and percentage
                tot = telem_data["ElevDefl"] / telem_data["ElevDeflPct"]
                tas = telem_data.get("TAS")  # m/s
                vc, vs0, vs1 = telem_data.get("DesignSpeed")  # m/s
                speed_factor = utils.scale_clamp(tas, (0, vc * 1.4), (0.0, 1.0))  # rough estimate that Vne is 1.4x Vc
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

            max_coeff_y = int(4096*self.max_elevator_coeff)
            ec = clamp(int(4096 * elevator_coeff), base_elev_coeff, max_coeff_y)
            pct_max_e = ec/max_coeff_y
            telem_data["_pct_max_e"] = pct_max_e
            self._ipc_telem["_pct_max_e"] = pct_max_e
            logging.debug(f"Elev Coef: {ec}")

            self.spring_y.negativeCoefficient = self.spring_y.positiveCoefficient = ec

            max_coeff_x = int(4096*self.max_aileron_coeff)
            ac = clamp(int(4096 * aileron_coeff), base_ailer_coeff, max_coeff_x)
            pct_max_a = ac / max_coeff_x
            telem_data["_pct_max_a"] = pct_max_a
            self._ipc_telem["_pct_max_a"] = pct_max_a
            logging.debug(f"Ailer Coef: {ac}")

            self.spring_x.positiveCoefficient = self.spring_x.negativeCoefficient = ac

            # update spring data
            self.spring.effect.setCondition(self.spring_y)
            self.spring.effect.setCondition(self.spring_x)

            # update constant forces
            cf_pitch = -_elevator_droop_term - _G_term # + _aoa_term
            cf_pitch = clamp(cf_pitch, -1.0, 1.0)

            # add force on lateral axis (sideways)
            if self.uncoordinated_turn_effect_enabled:
                _side_accel = -telem_data["AccBody"][0] * self.lateral_force_gain
            else:
                _side_accel = 0

            cf_roll = _side_accel

            cf = utils.Vector2D(cf_pitch, cf_roll)
            if cf.magnitude() > 1.0: 
                cf = cf.normalize()

            mag, theta = cf.to_polar()
            
            effects['control_weight'].constant(mag, theta*deg).start()
                # print(mag, theta*deg)
                # self.const_force.constant(mag, theta*deg).start()

            self.spring.start() # ensure spring is started

        elif ffb_type == 'pedals':
            if self.trim_following:
                if not self.telemffb_controls_axes:
                    logging.warning(
                        "TRIM FOLLOWING ENABLED BUT TELEMFFB IS NOT CONFIGURED TO SEND AXIS POSITION TO MSFS! Forcing to enable!")
                    self.telemffb_controls_axes = True  # Force sending of axis via simconnect if trim following is enabled
                rudder_trim = telem_data.get("RudderTrimPct", 0)

                rudder_trim = clamp(rudder_trim * self.rudder_trim_follow_gain_physical_x, -1, 1)
                virtual_rudder_x_offs = rudder_trim - (rudder_trim * self.rudder_trim_follow_gain_virtual_x)

                phys_rudder_x_offs = int(rudder_trim * 4096)
            else:
                phys_rudder_x_offs = 0
                virtual_rudder_x_offs = 0
            max_coeff_x = int(4096*self.max_rudder_coeff)
            x_coeff = clamp(int(4096 * rudder_coeff), base_rudder_coeff, max_coeff_x)
            pct_max_r = x_coeff / max_coeff_x
            telem_data["_pct_max_r"] = pct_max_r
            self._ipc_telem["_pct_max_r"] = pct_max_r
            self.spring_x.negativeCoefficient = self.spring_x.positiveCoefficient = x_coeff
            self.spring_x.cpOffset = phys_rudder_x_offs

            self.spring.effect.setCondition(self.spring_x)
            tas = telem_data.get("TAS")
            vc, vs0, vs1 = telem_data.get("DesignSpeed")  # m/s
            speed_factor = utils.scale_clamp(tas, (0, vc * 1.4), (0.0, 1.0))  # rough estimate that Vne is 1.4x Vc
            rud_force = rud_force * speed_factor
            # telem_data["RudForce"] = rud_force * speed_factor

            if self.telemffb_controls_axes:
                input_data = HapticEffect.device.getInput()
                phys_x, phys_y = input_data.axisXY()
                telem_data['phys_x'] = phys_x
                x_pos = phys_x - virtual_rudder_x_offs
                x_scale = clamp(self.rudder_x_axis_scale, 0, 1)

                if self.enable_custom_x_axis:
                    x_var = self.custom_x_axis
                    x_range = self.raw_x_axis_scale
                else:
                    x_var = 'AXIS_RUDDER_SET'
                    x_range = 16384

                pos_x_pos = utils.scale(x_pos, (-1, 1), (-x_range * x_scale, x_range * x_scale))

                if x_range != 1:
                    pos_x_pos = -int(pos_x_pos)
                else:
                    pos_x_pos = round(pos_x_pos, 5)

                self._simconnect.send_event_to_msfs(x_var, pos_x_pos)

            self.const_force.constant(rud_force, 270).start()
            self.spring.start()

    def on_event(self, event, *args):
        logging.info(f"on_event {event} {args}")

    def on_telemetry(self, telem_data):
        pass
        if telem_data.get("STOP",0):
            self.on_timeout()
            return
        effects["pause_spring"].spring().stop()
        # if telem_data["Parked"]: # Aircraft is parked, do nothing
        #     return
        #
        super().on_telemetry(telem_data)
        #
        ### Generic Aircraft Class Telemetry Handler
        if not "AircraftClass" in telem_data:
            telem_data["AircraftClass"] = "GenericAircraft"  # inject aircraft class into telemetry

        self._update_runway_rumble(telem_data)
        self._update_buffeting(telem_data)
        # self._update_flight_controls(telem_data)
        # self._decel_effect(telem_data)
        #
        # if self.flaps_motion_intensity > 0:
        #     flps = max(telem_data.get("Flaps", 0))
        #     self._update_flaps(flps)
        # retracts = telem_data.get("RetractableGear", 0)
        # if isinstance(retracts, list):
        #     retracts = max(retracts)
        # if (self.gear_motion_intensity > 0) and (retracts):
        #     gear = max(telem_data.get("Gear", 0))
        #     self._update_landing_gear(gear, telem_data.get("TAS"), spd_thresh_low=130 * kt2ms, spd_thresh_high=200 * kt2ms)
        #
        # self._decel_effect(telem_data)
        #
        # self._aoa_reduction_force_effect(telem_data)
        # if self.nosewheel_shimmy and telem_data.get("FFBType") == "pedals" and not telem_data.get("IsTaildragger", 0):
        #     self._update_nosewheel_shimmy(telem_data)

    def on_timeout(self):
        if not self.pause_spring.started:
            super().on_timeout()
        self.cyclic_spring_init = 0

        self.const_force.stop()
        self.spring.stop()
        if self.center_spring_on_pause:
            self.spring_x.negativeCoefficient = self.spring_x.positiveCoefficient = 4096
            self.spring_y.negativeCoefficient = self.spring_y.positiveCoefficient = 4096
            self.spring_x.cpOffset = self.spring_y.cpOffset = 0
            self.pause_spring.effect.setCondition(self.spring_x)
            self.pause_spring.effect.setCondition(self.spring_y)
            self.pause_spring.start()




class PropellerAircraft(Aircraft):
    """Generic Class for Prop aircraft"""

    # run on every telemetry frame
    def on_telemetry(self, telem_data):
        pass
        ### Propeller Aircraft Class Telemetry Handler
        if telem_data.get("N") == None:
            return
        telem_data["AircraftClass"] = "PropellerAircraft"  # inject aircraft class into telemetry
        if telem_data.get("STOP",0):
            self.on_timeout()
            return
        super().on_telemetry(telem_data)

        self.update_piston_engine_rumble(telem_data)
        #
        # if self.spoiler_motion_intensity > 0 or self.spoiler_buffet_intensity > 0:
        #     sp = max(telem_data.get("Spoilers", 0))
        #     self._update_spoiler(sp, telem_data.get("TAS"), spd_thresh_low=800*kt2ms, spd_thresh_hi=140*kt2ms )


class JetAircraft(Aircraft):
    """Generic Class for Jets"""
    # flaps_motion_intensity = 0.0

    _ab_is_playing = 0

    # run on every telemetry frame
    def on_telemetry(self, telem_data):
        pass
        # ## Jet Aircraft Telemetry Manager
        # if telem_data.get("N") == None:
        #     return
        # telem_data["AircraftClass"] = "JetAircraft"  # inject aircraft class into telemetry
        # if telem_data.get("STOP",0):
        #     self.on_timeout()
        #     return
        # super().on_telemetry(telem_data)
        #
        # if self.spoiler_motion_intensity > 0 or self.spoiler_buffet_intensity > 0:
        #     sp = max(telem_data.get("Spoilers", 0))
        #     self._update_spoiler(sp, telem_data.get("TAS"), spd_thresh_low=150*kt2ms, spd_thresh_hi=300*kt2ms )
        # self._update_jet_engine_rumble(telem_data)
        #
        # self._gforce_effect(telem_data)
        # self._update_ab_effect(telem_data)

class TurbopropAircraft(PropellerAircraft):

    def on_telemetry(self, telem_data):
        pass
        # if telem_data.get("N") == None:
        #     return
        # telem_data["AircraftClass"] = "TurbopropAircraft"  # inject aircraft class into telemetry
        # if telem_data.get("STOP",0):
        #     self.on_timeout()
        #     return
        # super().on_telemetry(telem_data)
        # # if self.spoiler_motion_intensity > 0 or self.spoiler_buffet_intensity > 0:
        # #     sp = max(telem_data.get("Spoilers", 0))
        # #     self._update_spoiler(sp, telem_data.get("TAS"), spd_thresh_low=120*kt2ms, spd_thresh_hi=260*kt2ms )
        # # if self.gforce_effect_enable and self.gforce_effect_enable_areyoureallysure:
        # #     super()._gforce_effect(telem_data)
        # self._update_jet_engine_rumble(telem_data)

class GliderAircraft(Aircraft):
    def _update_force_trim(self, telem_data, x_axis=True, y_axis=True):
        if not self.force_trim_enabled: 
            return
        self.spring = effects["dynamic_spring"].spring()
        ffb_type = telem_data.get("FFBType", "joystick")
        offs_x = 0
        offs_y = 0
        if ffb_type != "joystick":
            return
        if self.force_trim_button == 0:
            logging.warning("Force trim enabled but buttons not configured")
            telem_data['error'] = 1
            return

        # logging.debug(f"update_force_trim: x={x_axis}, y={y_axis}")
        input_data = HapticEffect.device.getInput()

        force_trim_pressed = input_data.isButtonPressed(self.force_trim_button)
        if self.force_trim_reset_button > 0:
            trim_reset_pressed = input_data.isButtonPressed(self.force_trim_reset_button)
        else:
            trim_reset_pressed = False
        x, y = input_data.axisXY()
        if force_trim_pressed:
            if x_axis:
                self.spring_x.positiveCoefficient = 2048
                self.spring_x.negativeCoefficient = 2048

                offs_x = round(x * 4096)
                self.spring_x.cpOffset = offs_x

                self.spring.effect.setCondition(self.spring_x)

            if y_axis:
                self.spring_y.positiveCoefficient = 2048
                self.spring_y.negativeCoefficient = 2048

                offs_y = round(y * 4096)
                self.spring_y.cpOffset = offs_y

                self.spring.effect.setCondition(self.spring_y)

            self.stick_center = [x,y]

            logging.info(f"Force Trim Disengaged:{round(x * 4096)}:{round(y * 4096)}")

            self.force_trim_release_active = 1

        if not force_trim_pressed and self.force_trim_release_active:

            self.spring_x.positiveCoefficient = clamp(int(4096 * self.aileron_spring_gain), 0, 4096)
            self.spring_x.negativeCoefficient = self.spring_x.positiveCoefficient

            self.spring_y.positiveCoefficient = clamp(int(4096 * self.elevator_spring_gain), 0, 4096)
            self.spring_y.negativeCoefficient = self.spring_y.positiveCoefficient
            if x_axis:
                offs_x = round(x * 4096)
                self.spring_x.cpOffset = offs_x
                self.spring.effect.setCondition(self.spring_x)

            if y_axis:
                offs_y = round(y * 4096)
                self.spring_y.cpOffset = offs_y
                self.spring.effect.setCondition(self.spring_y)

            # self.spring.start()
            self.stick_center = [x,y]

            logging.info(f"Force Trim Engaged :{offs_x}:{offs_y}")

            self.force_trim_release_active = 0

        if trim_reset_pressed or not self.force_trim_spring_init:
            if trim_reset_pressed:
                self.stick_center = [0, 0]

            if x_axis:
                self.spring_x.positiveCoefficient = clamp(int(4096 * self.aileron_spring_gain), 0, 4096)
                self.spring_x.negativeCoefficient = self.spring_x.positiveCoefficient
                cpO_x = round(self.cyclic_center[0]*4096)
                self.spring_x.cpOffset = cpO_x
                self.spring.effect.setCondition(self.spring_x)

            if y_axis:
                self.spring_y.positiveCoefficient = clamp(int(4096 * self.elevator_spring_gain), 0, 4096)
                self.spring_y.negativeCoefficient = self.spring_y.positiveCoefficient
                cpO_y = round(self.cyclic_center[1]*4096)
                self.spring_y.cpOffset = cpO_y
                self.spring.effect.setCondition(self.spring_y)

            # self.spring.start()
            self.force_trim_spring_init = 1
            logging.info("Trim Reset Pressed")
            return

        telem_data["StickXY"] = [x, y]
        telem_data["StickXY_offset"] = self.stick_center
        self.force_trim_x_offset = self.stick_center[0]
        self.force_trim_y_offset = self.stick_center[1]
    def on_telemetry(self, telem_data):
        pass
        # if telem_data.get("N") == None:
        #     return
        # telem_data["AircraftClass"] = "GliderAircraft"  # inject aircraft class into telemetry
        # if telem_data.get("STOP",0):
        #     self.on_timeout()
        #     return
        # super().on_telemetry(telem_data)
        # if self.force_trim_enabled:
        #     self._update_force_trim(telem_data, x_axis=self.aileron_force_trim, y_axis=self.elevator_force_trim)
        # if self.spoiler_motion_intensity > 0 or self.spoiler_buffet_intensity > 0:
        #     sp = max(telem_data.get("Spoilers", 0))
        #     self._update_spoiler(sp, telem_data.get("TAS"), spd_thresh_low=60*kt2ms, spd_thresh_hi=120*kt2ms )
        # if self.gforce_effect_enable and self.gforce_effect_enable_areyoureallysure:
        #     super()._gforce_effect(telem_data)

class Helicopter(Aircraft):
    """Generic Class for Helicopters"""
    buffeting_intensity = 0.0

    etl_start_speed = 6.0  # m/s
    etl_stop_speed = 22.0  # m/s
    etl_effect_intensity = 0.2  # [ 0.0 .. 1.0]
    etl_shake_frequency = 14.0  # value has been deprecated in favor of rotor RPM calculation
    overspeed_shake_start = 70.0  # m/s
    overspeed_shake_intensity = 0.2
    heli_engine_rumble_intensity = 0.12
    cpO_x = 0
    cpO_y = 0
    virtual_cyclic_x_offs = 0
    virtual_cyclic_y_offs = 0
    phys_cyclic_x_offs = 0
    phys_cyclic_y_offs = 0
    stepper_dict = {}
    trim_reset_complete = 1
    last_device_x = 0
    last_device_y = 0
    last_collective_y = 1
    last_pedal_x = 0
    collective_init = 0
    collective_ap_spring_gain = 1
    collective_dampening_gain = 1
    collective_spring_coeff_y = 0
    pedals_init = 0
    pedal_spring_gain = 1
    pedal_dampening_gain = 1
    pedal_spring_coeff_x = 0

    joystick_trim_follow_gain_physical_x = 0.3
    joystick_trim_follow_gain_virtual_x = 0.2
    joystick_trim_follow_gain_physical_y = 0.3
    joystick_trim_follow_gain_virtual_y = 0.2
    cyclic_physical_trim_x_offs = 0
    cyclic_physical_trim_y_offs = 0
    cyclic_virtual_trim_x_offs = 0
    cyclic_virtual_trim_y_offs = 0


    def __init__(self, name, **kwargs):

        super().__init__(name, **kwargs)
    def on_timeout(self):
        super().on_timeout()
        self.cyclic_spring_init = 0
        self.collective_init = 0
        self.pedals_init = 0

    def on_telemetry(self, telem_data):
        pass
        # self.speedbrake_motion_intensity = 0.0
        # if telem_data.get("N") == None:
        #     return
        # telem_data["AircraftClass"] = "Helicopter"  # inject aircraft class into telemetry
        # if telem_data.get("STOP",0):
        #     self.on_timeout()
        #     return
        # super().on_telemetry(telem_data)
        #
        # self._update_heli_controls(telem_data)
        # self._update_collective(telem_data)
        # # self._update_cyclic_trim(telem_data)
        # self._update_pedals(telem_data)
        # self._calc_etl_effect(telem_data, blade_ct=self.rotor_blade_count)
        # self._update_jet_engine_rumble(telem_data)
        # self._update_heli_engine_rumble(telem_data, blade_ct=self.rotor_blade_count)

    def step_value_over_time(self, key, value, timeframe_ms, dst_val):
        current_time_ms = time.time() * 1000  # Convert current time to milliseconds

        if key not in self.stepper_dict:
            self.stepper_dict[key] = {'value': value, 'dst_val': dst_val, 'start_time': current_time_ms, 'timeframe': timeframe_ms}

        data = self.stepper_dict[key]

        if data['value'] == data['dst_val']:
            del self.stepper_dict[key]
            return data['value']

        elapsed_time_ms = current_time_ms - data['start_time']

        if elapsed_time_ms >= timeframe_ms:
            data['value'] = data['dst_val']
            return data['dst_val']

        remaining_time_ms = timeframe_ms - elapsed_time_ms
        step_size = (data['dst_val'] - value) / remaining_time_ms

        data['value'] = round(value + step_size * elapsed_time_ms)
        # print(f"value out = {data['value']}")
        return data['value']

    def _update_heli_controls(self, telem_data):
        ffb_type = telem_data.get("FFBType", "joystick")
        ap_active = telem_data.get("APMaster", 0)
        trim_reset = max(telem_data.get("h145TrimRelease", 0), telem_data.get("h160TrimRelease", 0))
        self.spring = effects["cyclic_spring"].spring()

        if ffb_type == "joystick":
            if self.force_trim_enabled:

                if self.force_trim_button == 0:
                    logging.warning("Force trim enabled but buttons not configured")
                    telem_data['error'] = 1
                    return
                input_data = HapticEffect.device.getInput()

                force_trim_pressed = input_data.isButtonPressed(self.force_trim_button)
                if self.force_trim_reset_button > 0:
                    trim_reset_pressed = input_data.isButtonPressed(self.force_trim_reset_button)
                else:
                    trim_reset_pressed = False
                x, y = input_data.axisXY()
                telem_data['phys_x'] = x
                telem_data['phys_y'] = y
                if force_trim_pressed:
                    self.spring_x.positiveCoefficient = 0
                    self.spring_x.negativeCoefficient = 0

                    self.spring_y.positiveCoefficient = 0
                    self.spring_y.negativeCoefficient = 0

                    self.cpO_x = round(x * 4096)
                    self.spring_x.cpOffset = self.cpO_x

                    self.cpO_y = round(y * 4096)
                    self.spring_y.cpOffset = self.cpO_y

                    self.cyclic_center = [x, y]

                    logging.info(f"Force Trim Disengaged:{round(x * 4096)}:{round(y * 4096)}")

                    self.cyclic_trim_release_active = 1

                elif not force_trim_pressed and self.cyclic_trim_release_active:
                    self.spring_x.positiveCoefficient = clamp(int(4096 * self.cyclic_spring_gain), 0, 4096)
                    self.spring_x.negativeCoefficient = self.spring_x.positiveCoefficient

                    self.spring_y.positiveCoefficient = clamp(int(4096 * self.cyclic_spring_gain), 0, 4096)
                    self.spring_y.negativeCoefficient = self.spring_y.positiveCoefficient

                    self.cpO_x = round(x * 4096)
                    self.spring_x.cpOffset = self.cpO_x

                    self.cpO_y = round(y * 4096)
                    self.spring_y.cpOffset = self.cpO_y


                    self.cyclic_center = [x, y]

                    logging.info(f"Force Trim Engaged :{self.cpO_x}:{self.cpO_y}")
                    self._simconnect.send_event_to_msfs("ROTOR_TRIM_RESET", 0)


                    self.cyclic_trim_release_active = 0

                elif trim_reset_pressed or not self.trim_reset_complete:
                    self.cpO_x = self.step_value_over_time("center_x", self.cpO_x, 2500, 0)
                    self.cpO_y = self.step_value_over_time("center_y", self.cpO_y, 2500, 0)

                    if self.cpO_x == 0 and self.cpO_y == 0:
                        self.trim_reset_complete = 1
                    else:
                        self.trim_reset_complete = 0
                    # self.cpO_x, self.cpO_y = 0, 0


                    self.spring_x.positiveCoefficient = clamp(int(4096 * self.cyclic_spring_gain), 0, 4096)
                    self.spring_x.negativeCoefficient = self.spring_x.positiveCoefficient

                    self.spring_y.positiveCoefficient = clamp(int(4096 * self.cyclic_spring_gain), 0, 4096)
                    self.spring_y.negativeCoefficient = self.spring_y.positiveCoefficient

                    # self.cpO_x = round(self.cyclic_center[0] * 4096)
                    # self.cpO_y = round(self.cyclic_center[1] * 4096)

                    self.spring_x.cpOffset = self.cpO_x
                    self.spring_y.cpOffset = self.cpO_y
                    self._simconnect.send_event_to_msfs("ROTOR_TRIM_RESET", 0)

                    logging.info("Trim Reset Pressed")

                elif not self.cyclic_spring_init:
                    self.cyclic_center = [0, 0]

                    input_data = HapticEffect.device.getInput()


                    # force_trim_pressed = input_data.isButtonPressed(self.force_trim_reset_button)
                    phys_x, phys_y = input_data.axisXY()
                    telem_data['phys_x'] = phys_x
                    telem_data['phys_y'] = phys_y
                    self.spring_x.positiveCoefficient = clamp(int(4096 * self.cyclic_spring_gain), 0, 4096)
                    self.spring_x.negativeCoefficient = self.spring_x.positiveCoefficient

                    self.spring_y.positiveCoefficient = clamp(int(4096 * self.cyclic_spring_gain), 0, 4096)
                    self.spring_y.negativeCoefficient = self.spring_y.positiveCoefficient

                    if telem_data.get("SimOnGround", 1):
                        self.cpO_x = 0
                        self.cpO_y = 0
                    else:
                        self.cpO_x = round(self.last_device_x * 4096)
                        self.cpO_y = round(self.last_device_y * 4096)

                    self.spring_x.cpOffset = self.cpO_x
                    self.spring_y.cpOffset = self.cpO_y
                    self.spring.effect.setCondition(self.spring_x)
                    self.spring.effect.setCondition(self.spring_y)
                    self.spring.start()
                    if (self.cpO_x/4096 - 0.2 < phys_x < self.cpO_x/4096 + 0.2) and (self.cpO_y/4096 - 0.2 < phys_y < self.cpO_y/4096 + 0.2):
                        #dont start sending position until physical stick has centered
                        self.cyclic_spring_init = 1
                        logging.info("Cyclic Spring Initialized")
                    else:
                        return

                telem_data["StickXY"] = [x, y]
                telem_data["StickXY_offset"] = self.cyclic_center
            else:
                self.spring.stop()

            if self.telemffb_controls_axes:
                input_data = HapticEffect.device.getInput()
                phys_x, phys_y = input_data.axisXY()
                telem_data['phys_x'] = phys_x
                telem_data['phys_y'] = phys_y
                self._update_cyclic_trim(telem_data)

                x_pos = phys_x - self.cyclic_virtual_trim_x_offs
                y_pos = phys_y - self.cyclic_virtual_trim_y_offs

                x_scale = clamp(self.joystick_x_axis_scale, 0, 1)
                y_scale = clamp(self.joystick_y_axis_scale, 0, 1)

                if self.cyclic_spring_init or not self.force_trim_enabled:
                    if self.enable_custom_x_axis:
                        x_var = self.custom_x_axis
                        x_range = self.raw_x_axis_scale
                    else:
                        x_var = 'AXIS_CYCLIC_LATERAL_SET'
                        x_range = 16384
                    if self.enable_custom_y_axis:
                        y_var = self.custom_y_axis
                        y_range = self.raw_y_axis_scale
                    else:
                        y_var = 'AXIS_CYCLIC_LONGITUDINAL_SET'
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

                self.last_device_x, self.last_device_y = phys_x, phys_y

            if self.anything_has_changed("cyclic_gain", self.cyclic_spring_gain):  # check if spring gain setting has been modified in real time
                self.spring_x.positiveCoefficient = clamp(int(4096 * self.cyclic_spring_gain), 0, 4096)
                self.spring_x.negativeCoefficient = self.spring_x.positiveCoefficient

                self.spring_y.positiveCoefficient = clamp(int(4096 * self.cyclic_spring_gain), 0, 4096)
                self.spring_y.negativeCoefficient = self.spring_y.positiveCoefficient

            self.spring_x.cpOffset = int(self.cpO_x) + self.cyclic_physical_trim_x_offs
            self.spring_y.cpOffset = int(self.cpO_y) + self.cyclic_physical_trim_y_offs
            self.spring.effect.setCondition(self.spring_x)
            self.spring.effect.setCondition(self.spring_y)
            if self.force_trim_enabled:
                self.spring.start()

    def _update_cyclic_trim(self, telem_data):
        if telem_data.get("FFBType", None) != 'joystick':
            return
        if not self.trim_following:
            return
        cyclic_x_trim = telem_data.get("CyclicTrimX", 0)
        cyclic_y_trim = telem_data.get("CyclicTrimY", 0)

        cyclic_x_trim = clamp(cyclic_x_trim * self.joystick_trim_follow_gain_physical_x * self.joystick_x_axis_scale, -1, 1)
        cyclic_y_trim = clamp(cyclic_y_trim * self.joystick_trim_follow_gain_physical_y * self.joystick_y_axis_scale, -1, 1)

        # print(f"x:{cyclic_x_trim}, y:{cyclic_y_trim}")

        self.cyclic_physical_trim_x_offs = round(cyclic_x_trim * 4096)
        self.cyclic_physical_trim_y_offs = round(cyclic_y_trim * 4096)
        self.cyclic_virtual_trim_x_offs = cyclic_x_trim - (cyclic_x_trim * self.joystick_trim_follow_gain_virtual_x)
        self.cyclic_virtual_trim_y_offs = cyclic_y_trim - (cyclic_y_trim * self.joystick_trim_follow_gain_virtual_y)


    def _update_pedals(self, telem_data):
        if telem_data.get("FFBType") != 'pedals':
            return

        if self.telemffb_controls_axes:
            input_data = HapticEffect.device.getInput()
            phys_x, phys_y = input_data.axisXY()
            x_scale = clamp(self.rudder_x_axis_scale, 0, 1)

            self.spring = effects["pedal_ap_spring"].spring()
            self.damper = effects["pedal_damper"].damper()

            pedal_pos = telem_data.get("TailRotorPedalPos")
            input_data = HapticEffect.device.getInput()
            phys_x, phys_y = input_data.axisXY()
            telem_data['phys_x'] = phys_x
            if not self.pedals_init:

                self.spring_x.negativeCoefficient = self.spring_x.positiveCoefficient = self.pedal_spring_coeff_x
                if telem_data.get("SimOnGround", 1):
                    self.cpO_x = 0
                else:
                    # print(f"last_colelctive_y={self.last_collective_y}")
                    self.cpO_x = round(4096 * self.last_pedal_x)

                self.spring_x.positiveCoefficient = self.spring_x.negativeCoefficient = round(4096 * utils.clamp(self.pedal_spring_gain, 0, 1))

                self.spring_x.cpOffset = self.cpO_x

                self.spring.effect.setCondition(self.spring_x)
                self.damper.damper(coef_x=int(4096 * self.pedal_dampening_gain)).start()
                self.spring.start()
                logging.debug(f"self.cpO_x:{self.cpO_x}, phys_x:{phys_x}")
                if self.cpO_x / 4096 - 0.1 < phys_x < self.cpO_x / 4096 + 0.1:
                    # dont start sending position until physical pedals have centered
                    self.pedals_init = 1
                    logging.info("Pedals Initialized")
                    self.spring.stop()
                else:
                    return

            self.last_pedal_x = phys_x

            if self.enable_custom_x_axis:
                x_var = self.custom_x_axis
                x_range = self.raw_x_axis_scale
            else:
                x_var = 'ROTOR_AXIS_TAIL_ROTOR_SET'
                x_range = 16384

            pos_x_pos = utils.scale(phys_x, (-1, 1), (-x_range * x_scale, x_range * x_scale))

            if x_range != 1:
                pos_x_pos = -int(pos_x_pos)
            else:
                pos_x_pos = round(pos_x_pos, 5)

            self._simconnect.send_event_to_msfs(x_var, pos_x_pos)

    def _update_collective(self, telem_data):
        if telem_data.get("FFBType") != 'collective':
            return
        if not self.telemffb_controls_axes:
            # logging.error(
            #     "Aircraft is configured as class HPGHelicopter.  For proper integration, TelemFFB must send axis position to MSFS.\n\nPlease enable 'telemffb_controls_axes' in your config and unbind the collective axes in MSFS settings")
            return
        self.spring = effects["collective_ap_spring"].spring()
        self.damper = effects["collective_damper"].damper()
        # collective_tr = telem_data.get("h145CollectiveRelease", 0)
        # afcs_mode = telem_data.get("h145CollectiveAfcsMode", 0)
        collective_pos = telem_data.get("CollectivePos", 0)

        # input_data = HapticEffect.device.getInput()
        # phys_x, phys_y = input_data.axisXY()
        # SimVar("h145CollectiveRelease", "L:H145_SDK_AFCS_COLLECTIVE_TRIM_IS_RELEASED", "bool"),
        # SimVar("h145CollectiveAfcsMode", "L:H145_SDK_AFCS_MODE_COLLECTIVE", "number"),
        # SimVar("CollectivePos", "COLLECTIVE POSITION", "percent over 100"),
        input_data = HapticEffect.device.getInput()
        phys_x, phys_y = input_data.axisXY()
        telem_data['phys_y'] = phys_y
        if not self.collective_init:
            self.spring_y.negativeCoefficient = self.spring_y.positiveCoefficient = self.collective_spring_coeff_y
            if telem_data.get("SimOnGround", 1):
                self.cpO_y = 4096
            else:
                # print(f"last_colelctive_y={self.last_collective_y}")
                self.cpO_y = round(4096 * self.last_collective_y)

            self.spring_y.positiveCoefficient = self.spring_y.negativeCoefficient = round(
                4096 * utils.clamp(self.collective_ap_spring_gain, 0, 1))

            self.spring_y.cpOffset = self.cpO_y

            self.spring.effect.setCondition(self.spring_y)
            self.damper.damper(coef_y=int(4096*self.collective_dampening_gain)).start()
            self.spring.start()
            # print(f"self.cpO_y:{self.cpO_y}, phys_y:{phys_y}")
            if self.cpO_y/4096 - 0.1 < phys_y < self.cpO_y/4096 + 0.1:
                # dont start sending position until physical stick has centered
                self.collective_init = 1
                logging.info("Collective Initialized")
            else:
                return
        self.last_collective_y = phys_y
        self.cpO_y = round(4096 * utils.clamp(phys_y, -1, 1))
        # print(self.cpO_y)
        self.spring_y.cpOffset = self.cpO_y

        self.damper.damper(coef_y=int(4096*self.collective_dampening_gain)).start()
        self.spring_y.negativeCoefficient = self.spring_y.positiveCoefficient = round(
            self.collective_spring_coeff_y / 2)

        self.spring.effect.setCondition(self.spring_y)
        self.spring.start()

        if self.enable_custom_y_axis:
            y_var = self.custom_y_axis
            y_range = self.raw_y_axis_scale
        else:
            y_var = 'AXIS_COLLECTIVE_SET'
            y_range = 16384

        pos_y_pos = utils.scale(phys_y, (-1, 1), (-y_range, y_range))

        if y_range != 1:
            pos_y_pos = -int(pos_y_pos)
        else:
            pos_y_pos = round(pos_y_pos, 5)

        if self.collective_init:
            self._simconnect.send_event_to_msfs(y_var, pos_y_pos)




class HPGHelicopter(Helicopter):
    sema_x_max = 5
    sema_y_max = 5
    afcs_step_size = 2
    collective_init = 0
    collective_ap_spring_gain = 1
    collective_dampening_gain = 1
    collective_spring_coeff_y = 0
    hands_on_deadzone = 0.1
    hands_off_deadzone = 0.02
    hands_on_active = 0
    hands_on_x_active = 0
    hands_on_y_active = 0
    send_individual_hands_on = 0
    vrs_effect_enable: bool = True
    vrs_effect_intensity = 0

    def __init__(self, name, **kwargs):
        super().__init__(name, **kwargs)
        input_data = HapticEffect.device.getInput()
        self.phys_x, self.phys_y = input_data.axisXY()
        self.cpO_y = round(self.phys_y * 4096)
        self.collective_spring_coeff_y = round(4096 * utils.clamp(self.collective_ap_spring_gain, 0, 1))
        self.hands_on_active = 0
        self.hands_on_x_active = 0
        self.hands_on_y_active = 0

    def on_telemetry(self, telem_data):
        pass
        # super().on_telemetry(telem_data)
        # self._update_vrs_effect(telem_data)

    def on_timeout(self):
        super().on_timeout()
        self.collective_init = 0
        self.pedals_init = 0

    def check_hands_on(self, percent):
        input_data = HapticEffect.device.getInput()
        phys_x, phys_y = input_data.axisXY()

        # Convert phys input to +/-4096
        phys_x = round(phys_x * 4096)
        phys_y = round(phys_y * 4096)

        ref_x = self.cpO_x
        ref_y = self.cpO_y

        # Calculate the threshold values based on the input percentage
        threshold = 4096 * percent

        # Calculate the deviation percentages in decimal form
        deviation_x = abs(phys_x - ref_x) / 4096
        deviation_y = abs(phys_y - ref_y) / 4096

        # Check if either phys_x or phys_y exceeds the threshold
        x_exceeds_threshold = abs(phys_x - ref_x) > threshold
        y_exceeds_threshold = abs(phys_y - ref_y) > threshold
        master_exceeds_threshold = x_exceeds_threshold or y_exceeds_threshold

        result = {
            "master_result": master_exceeds_threshold,
            "x_result": x_exceeds_threshold,
            "x_deviation": deviation_x,
            "y_result": y_exceeds_threshold,
            "y_deviation": deviation_y,
        }

        return result
    def _update_heli_controls(self, telem_data):

        super()._update_heli_controls(telem_data)
        ffb_type = telem_data.get("FFBType", "joystick")
        ap_active = telem_data.get("APMaster", 0)
        trim_reset = max(telem_data.get("h145TrimRelease", 0), telem_data.get("h160TrimRelease", 0))

        if ffb_type == "joystick":
            if not self.telemffb_controls_axes:
                logging.error(
                    "Aircraft is configured as class HPGHelicopter.  For proper integration, TelemFFB must send axis position to MSFS.\n\nPlease enable 'telemffb_controls_axes' in your config and unbind the cyclic axes in MSFS settings")
                telem_data['error'] = 1
                return
            sema_x = telem_data.get("h145SEMAx")
            sema_y = telem_data.get("h145SEMAy")

            # sema_x_avg = self.smoother.get_rolling_average('s_sema_x', sema_x, window_ms=500)
            # sema_y_avg = self.smoother.get_rolling_average('s_sema_y', sema_y, window_ms=500)
            sema_x_avg = sema_x
            sema_y_avg = sema_y

            if not trim_reset:
                sx = round(abs(sema_x_avg), 3)
                sy = round(abs(sema_y_avg), 3)
                if 100 >= sx > 50:
                    self.afcsx_step_size = 5
                elif 49.999 > sx > 20:
                    self.afcsx_step_size = 3
                elif 19.999 > sx > 10:
                    self.afcsx_step_size = 2
                elif 9.999 > sx > 5:
                    self.afcsx_step_size = 1
                elif 4.999 > sx >= 0:
                    self.afcsx_step_size = 1
                else:
                    self.afcsx_step_size = 0


                if 100 >= sy > 50:
                    self.afcsy_step_size = 5
                elif 49.999 > sy > 20:
                    self.afcsy_step_size = 3
                elif 19.999 > sy > 10:
                    self.afcsy_step_size = 2
                elif 9.999 > sx > 5:
                    self.afcsy_step_size = 1
                elif 4.999 > sy >= 0:
                    self.afcsy_step_size = 1
                else:
                    self.afcsy_step_size = 0


                if not (self.hands_on_x_active or self.hands_on_active):
                    if abs(sema_x_avg) > self.sema_x_max:
                        # print(f"sema_x:{sema_x}")
                        if sema_x_avg > self.sema_x_max:
                            self.cpO_x -= self.afcsx_step_size
                        elif sema_x_avg < -self.sema_x_max:
                            self.cpO_x += self.afcsx_step_size

                if not (self.hands_on_y_active or self.hands_on_active):
                    if abs(sema_y_avg) > self.sema_y_max:
                        # print(f"sema_y:{sema_y}")
                        if sema_y_avg > self.sema_y_max:
                            self.cpO_y -= self.afcsy_step_size
                        elif sema_y_avg < -self.sema_y_max:
                            self.cpO_y += self.afcsy_step_size
            self.spring_x.cpOffset = int(self.cpO_x)
            self.spring_y.cpOffset = int(self.cpO_y)
            self.spring.effect.setCondition(self.spring_x)
            self.spring.effect.setCondition(self.spring_y)

            # hands_off_deadzone = 0.02
            if (telem_data.get("h145HandsOnCyclic", 0) or telem_data.get("h160HandsOnCyclic", 0)):
                hands_on_dict = self.check_hands_on(self.hands_off_deadzone)
            else:
                hands_on_dict = self.check_hands_on(self.hands_on_deadzone)
            hands_on_either = hands_on_dict["master_result"]
            hands_on_x = hands_on_dict["x_result"]
            dev_x = hands_on_dict["x_deviation"]
            hands_on_y = hands_on_dict["y_result"]
            dev_y = hands_on_dict["y_deviation"]
            if self.send_individual_hands_on:
                if hands_on_x:
                    self._simconnect.set_simdatum_to_msfs("L:FFB_HANDS_ON_CYCLICX", 1, units="number")
                    self.hands_on_x_active = True

                else:
                    self._simconnect.set_simdatum_to_msfs("L:FFB_HANDS_ON_CYCLICX", 0, units="number")
                    self.hands_on_x_active = False

                if hands_on_y:
                    self._simconnect.set_simdatum_to_msfs("L:FFB_HANDS_ON_CYCLICY", 1, units="number")
                    self.hands_on_y_active = True
                else:
                    self._simconnect.set_simdatum_to_msfs("L:FFB_HANDS_ON_CYCLICY", 0, units="number")
                    self.hands_on_y_active = False
            else:
                if hands_on_either:
                    self._simconnect.set_simdatum_to_msfs("L:FFB_HANDS_ON_CYCLIC", 1, units="number")
                    self.hands_on_active = True
                else:
                    self._simconnect.set_simdatum_to_msfs("L:FFB_HANDS_ON_CYCLIC", 0, units="number")
                    self.hands_on_active = False

            telem_data["hands_on"] = int(hands_on_either)
            telem_data["hands_on_x"] = int(hands_on_x)
            telem_data["hands_on_y"] = int(hands_on_y)
            telem_data["deviation_x"] = dev_x
            telem_data["deviation_y"] = dev_y

            self.spring.start()

    def _update_cyclic_trim(self, telem_data):
        # Trimming is handled by the AFCS integration - override parent class function
        pass
    def _update_pedals(self, telem_data):

        if telem_data.get("FFBType") != 'pedals':
            return

        if self.telemffb_controls_axes:
            input_data = HapticEffect.device.getInput()
            phys_x, phys_y = input_data.axisXY()
            telem_data['phys_x'] = phys_x

            x_scale = clamp(self.rudder_x_axis_scale, 0, 1)

            self.spring = effects["pedal_ap_spring"].spring()
            self.damper = effects["pedal_damper"].damper()

            pedal_pos = telem_data.get("TailRotorPedalPos")
            pedal_cpO_x = round(4096*pedal_pos)

            if not self.pedals_init:

                self.spring_x.negativeCoefficient = self.spring_x.positiveCoefficient = self.pedal_spring_coeff_x
                if telem_data.get("SimOnGround", 1):
                    self.cpO_x = 0
                else:
                    # print(f"last_colelctive_y={self.last_collective_y}")
                    self.cpO_x = round(4096 * self.last_pedal_x)

                self.spring_x.positiveCoefficient = self.spring_x.negativeCoefficient = round(
                    4096 * utils.clamp(self.pedal_spring_gain, 0, 1))

                self.spring_x.cpOffset = self.cpO_x

                self.spring.effect.setCondition(self.spring_x)
                self.damper.damper(coef_x=int(4096 * self.pedal_dampening_gain)).start()
                self.spring.start()
                logging.debug(f"self.cpO_x:{self.cpO_x}, phys_x:{phys_x}")
                if self.cpO_x / 4096 - 0.1 < phys_x < self.cpO_x / 4096 + 0.1:
                    # dont start sending position until physical pedals have centered
                    self.pedals_init = 1
                    logging.info("Pedals Initialized")
                else:
                    return

            if self.enable_custom_x_axis:
                x_var = self.custom_x_axis
                x_range = self.raw_x_axis_scale
            else:
                x_var = 'ROTOR_AXIS_TAIL_ROTOR_SET'
                x_range = 16384

            pos_x_pos = utils.scale(phys_x, (-1, 1), (-x_range * x_scale, x_range * x_scale))

            if x_range != 1:
                pos_x_pos = -int(pos_x_pos)
            else:
                pos_x_pos = round(pos_x_pos, 5)

            self.last_pedal_x = phys_x

            self._simconnect.send_event_to_msfs(x_var, pos_x_pos)

    def _update_collective(self, telem_data):
        if telem_data.get("FFBType") != 'collective':
            return
        if not self.telemffb_controls_axes:
            logging.error("Aircraft is configured as class HPGHelicopter.  For proper integration, TelemFFB must send axis position to MSFS.\n\nPlease enable 'telemffb_controls_axes' in your config and unbind the collective axes in MSFS settings")
            telem_data['error'] = 1
            return
        self.spring = effects["collective_ap_spring"].spring()
        self.damper = effects["collective_damper"].damper()
        collective_tr = max(telem_data.get("h145CollectiveRelease", 0), telem_data.get("h160CollectiveRelease", 0))
        afcs_mode = max(telem_data.get("h145CollectiveAfcsMode", 0), telem_data.get("h160CollectiveAfcsMode", 0))
        collective_pos = telem_data.get("CollectivePos", 0)

        # input_data = HapticEffect.device.getInput()
        # phys_x, phys_y = input_data.axisXY()
        # SimVar("h145CollectiveRelease", "L:H145_SDK_AFCS_COLLECTIVE_TRIM_IS_RELEASED", "bool"),
        # SimVar("h145CollectiveAfcsMode", "L:H145_SDK_AFCS_MODE_COLLECTIVE", "number"),
        # SimVar("CollectivePos", "COLLECTIVE POSITION", "percent over 100"),
        input_data = HapticEffect.device.getInput()
        phys_x, phys_y = input_data.axisXY()
        telem_data['phys_y'] = phys_y
        if not self.collective_init:


            self.spring_y.negativeCoefficient = self.spring_y.positiveCoefficient = self.collective_spring_coeff_y
            if telem_data.get("SimOnGround", 1):
                self.cpO_y = 4096
            else:
                self.cpO_y = round(4096 * self.last_collective_y)

            self.spring_y.positiveCoefficient = self.spring_y.negativeCoefficient = round(4096 * utils.clamp(self.collective_ap_spring_gain, 0, 1))

            self.spring_y.cpOffset = self.cpO_y

            self.spring.effect.setCondition(self.spring_y)
            self.damper.damper(coef_y=4096).start()
            self.spring.start()
            if self.last_collective_y - 0.2 < phys_y < self.last_collective_y + 0.2:
                # dont start sending position until physical stick has centered
                self.collective_init = 1
                logging.info("Collective Initialized")
            else:
                return
        self.last_collective_y = phys_y

        if afcs_mode == 0:
            if collective_tr:

                self.cpO_y = round(4096*utils.clamp(phys_y, -1, 1))
                # print(self.cpO_y)
                self.spring_y.cpOffset = self.cpO_y

                self.damper.damper(coef_y=0).start()
                self.spring_y.negativeCoefficient = self.spring_y.positiveCoefficient = round(self.collective_spring_coeff_y/2)

                self.spring.effect.setCondition(self.spring_y)
                self.spring.start()

                if self.enable_custom_y_axis:
                    y_var = self.custom_y_axis
                    y_range = self.raw_y_axis_scale
                else:
                    y_var = 'AXIS_COLLECTIVE_SET'
                    y_range = 16384

                pos_y_pos = utils.scale(phys_y, (-1, 1), (-y_range, y_range))

                if y_range != 1:
                    pos_y_pos = -int(pos_y_pos)
                else:
                    pos_y_pos = round(pos_y_pos, 5)

                if self.collective_init:
                    self._simconnect.send_event_to_msfs(y_var, pos_y_pos)


            else:
                self.spring_y.cpOffset = self.cpO_y

                self.damper.damper(coef_y=4096).start()
                self.spring_y.negativeCoefficient = self.spring_y.positiveCoefficient = round(self.collective_spring_coeff_y / 2)

                self.spring.effect.setCondition(self.spring_y)
                self.spring.start()

        else:
            if collective_tr:

                self.cpO_y = round(4096*utils.clamp(phys_y, -1, 1))
                # print(self.cpO_y)
                self.spring_y.cpOffset = self.cpO_y

                self.damper.damper(coef_y=0).start()
                self.spring_y.negativeCoefficient = self.spring_y.positiveCoefficient = round(self.collective_spring_coeff_y/2)

                self.spring.effect.setCondition(self.spring_y)
                self.spring.start()

                if self.enable_custom_y_axis:
                    y_var = self.custom_y_axis
                    y_range = self.raw_y_axis_scale
                else:
                    y_var = 'AXIS_COLLECTIVE_SET'
                    y_range = 16384

                pos_y_pos = utils.scale(phys_y, (-1, 1), (-y_range, y_range))

                if y_range != 1:
                    pos_y_pos = -int(pos_y_pos)
                else:
                    pos_y_pos = round(pos_y_pos, 5)

                if self.collective_init:
                    self._simconnect.send_event_to_msfs(y_var, pos_y_pos)

            else:
                collective_pos = telem_data.get("CollectivePos", 0)
                self.cpO_y = round(utils.scale(collective_pos,(0, 1), (4096, -4096)))
                self.spring_y.cpOffset = self.cpO_y
                self.damper.damper(coef_y=256).start()

                self.spring.effect.setCondition(self.spring_y)
                self.spring.start()
    def _update_vrs_effect(self, telem_data):
        vrs_onset = telem_data.get("HPGVRSDatum", 0)
        vrs_certain = telem_data.get("HPGVRSIsInVRS", 0)


        if vrs_certain:
            vrs_intensity = 1.0
        elif vrs_onset == 1:
            vrs_intensity = .33
        elif vrs_onset == 2:
            vrs_intensity = .66
        else:
            vrs_intensity = 0


        if vrs_intensity and self.vrs_effect_intensity and self.vrs_effect_enable:
            effects["vrs_buffet"].periodic(10, self.vrs_effect_intensity * vrs_intensity, utils.RandomDirectionModulator).start()
        else:
            effects.dispose("vrs_buffet")




