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
import logging
import math
import socket
import sys
import time
from ctypes import byref, cast, sizeof
from math import atan2, cos, radians, sin, sqrt
from pprint import pprint
from time import sleep
from typing import Dict, List

import telemffb.utils as utils
from telemffb.hw.ffb_rhino import (FFBReport_Input, FFBReport_SetCondition,
                                   HapticEffect)
from telemffb.sim.aircraft_base import AircraftBase, HPFs, LPFs, effects
from telemffb.utils import Derivative, Dispenser, HighPassFilter, clamp, overrides

deg = 180 / math.pi
slugft3 = 0.00194032  # SI to slugft3
rad = 0.0174532925
ft = 3.28084  # m to ft
kt = 1.94384  # ms to kt
kt2ms = 0.514444  # knots to m/s
ms2kt = 1.943844  # m/s to knot
vsound = 290.07 # m/s, speed of sound at sea level in ISA condition
P0 = 101325 # Pa, ISA static pressure at sealevel
std_air_pressure = 1.225  # kg/m^3

EFFECT_SQUARE = 3
EFFECT_SINE = 4
EFFECT_TRIANGLE = 5
EFFECT_SAWTOOTHUP = 6
EFFECT_SAWTOOTHDOWN = 7

class Aircraft(AircraftBase):
    """Base class for Aircraft based FFB"""

    speedbrake_motion_intensity: float = 0.12  # peak vibration intensity when speed brake is moving, 0 to disable
    speedbrake_buffet_intensity: float = 0.15  # peak buffeting intensity when speed brake deployed,  0 to disable

    spoiler_motion_intensity: float = 0.0  # peak vibration intensity when spoilers is moving, 0 to disable
    spoiler_buffet_intensity: float = 0.15  # peak buffeting intensity when spoilers deployed,  0 to disable


    flaps_motion_intensity: float = 0.12  # peak vibration intensity when flaps are moving, 0 to disable
    flaps_buffet_intensity: float = 0.0  # peak buffeting intensity when flaps are deployed,  0 to disable

    canopy_motion_intensity: float = 0.12  # peak vibration intensity when canopy is moving, 0 to disable
    canopy_buffet_intensity: float = 0.0  # peak buffeting intensity when canopy is open during flight,  0 to disable

    afterburner_effect_intensity = 0.2  # peak intensity for afterburner rumble effect
    jet_engine_rumble_intensity = 0.12  # peak intensity for jet engine rumble effect
    jet_engine_rumble_freq = 45  # base frequency for jet engine rumble effect (Hz)

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
    trim_release_spring_gain = 0

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

    aileron_expo: int = 0
    elevator_expo: int = 0
    rudder_expo: int = 0

    vne_override: int = 0

    @classmethod
    def set_simconnect(cls, sc):
        cls._simconnect = sc

    def __init__(self, name, **kwargs) -> None:
        super().__init__(name)


        # clear any existing effects
        for e in effects.values(): e.destroy()
        effects.clear()
        # self.spring = HapticEffect().spring()
        self._spring_handle =  effects["spring"].spring()
        self._spring_handle.name = "spring"
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
        self.force_disable_collective_gain = True
        self.trim_release_spring_gain = 0

        self.force_trim_release_active = 0
        self.force_trim_spring_init = 0
        self.stick_center = [0, 0]  # x, y

        self.force_trim_x_offset = 0
        self.force_trim_y_offset = 0

        self.telemffb_controls_axes = False
        self.local_disable_axis_control = False

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

        self.enable_stick_shaker = 0
        self.stick_shaker_intensity = 0

        self.ap_following = True

        self.use_fbw_for_ap_follow = True

        self.invert_ap_x_axis = False
        self.max_elevator_coeff = 0.5
        self.max_aileron_coeff = 0.5
        self.max_rudder_coeff = 0.5

        self.xplane_axis_override_active = False

    def _update_nosewheel_shimmy(self, telem_data):
        curve = 2.5
        # freq = 8
        freq_lo = 8
        freq_hi = 16
        brakes = telem_data.get("Brakes", (0, 0))
        on_ground = telem_data.get("SimOnGround", 0)
        wow = sum(telem_data.get("WeightOnWheels", 0))
        if not wow or not on_ground:
            effects.dispose("nw_shimmy")
            return
        gs = telem_data.get("GroundSpeed", 0)

        freq = int(utils.scale(gs, (self.nosewheel_shimmy_min_speed, self.nosewheel_shimmy_min_speed*3), (freq_lo, freq_hi)))
        logging.debug(f"brakes = {brakes}")
        avg_brakes = sum(brakes) / len(brakes)
        if avg_brakes >= self.nosewheel_shimmy_min_brakes and gs > self.nosewheel_shimmy_min_speed:
            shimmy = utils.non_linear_scaling(avg_brakes, self.nosewheel_shimmy_min_brakes, 1.0, curvature=curve) * self.nosewheel_shimmy_intensity
            logging.debug(f"Nosewheel Shimmy intensity calculation: (BrakesPct:{avg_brakes} | GS:{gs} | RT Intensity: {shimmy}")
            effects["nw_shimmy"].periodic(freq, shimmy, 90).start()
        else:
            effects.dispose("nw_shimmy")

    def expocurve(self,x, k):
        # expo function for + k: y = (1-k)x + k( (1-e^(-ax)) / (1-e^-a))
        #       for negative k: y = (1+k)x + -k(e^(a(x-1))-e^(-a)) / (1-e^(-a))
        #   x = orig pct_max
        #   y = new pct_max
        #   k = expo value 0-1
        #   a = alpha, controls how much to bend the curve.
        #       a=5.5 gives approx 2x increase at 25% orig pct_max with k=0.5, 3x at 25% with k=1
        #               and 1/2x decrease with k=-0.5, 1/3x with k=-1 at 75%
        newvalue = 0
        expo_a = 5.5  # alpha
        if k >= 0:
            newvalue = (1 - k) * x + k * (1 - math.exp(-expo_a * x)) / (1 - math.exp(-expo_a))
        else:
            newvalue = (1 + k) * x + (-k) * (math.exp(expo_a * (x - 1)) - math.exp(-expo_a)) / (1 - math.exp(-expo_a))
        #print(f'expo input:{x} k:{k} output:{newvalue}')
        return newvalue
    def _update_fbw_flight_controls(self, telem_data):
        ffb_type = telem_data.get("FFBType", "joystick")
        if self._sim_is_msfs():
            ap_active = telem_data.get("APMaster", 0)
        if self._sim_is_xplane():
            ap_active = telem_data.get("APServos", 0)

        self._spring_handle.name = "fbw_spring"
        if ffb_type == "joystick":

            if self.trim_following and self.telemffb_controls_axes and not self.local_disable_axis_control:

                elev_trim = telem_data.get("ElevTrimPct", 0)

                elev_trim = self.dampener.dampen_value(elev_trim, '_elev_trim', derivative_hz=5, derivative_k=0.15)

                # print(f"raw:{raw_elev_trim}, smooth:{elev_trim}")
                aileron_trim = telem_data.get("AileronTrimPct", 0)

                aileron_trim = clamp(aileron_trim * self.joystick_trim_follow_gain_physical_x, -1, 1)
                virtual_stick_x_offs = aileron_trim - (aileron_trim * self.joystick_trim_follow_gain_virtual_x)

                elev_trim = clamp(elev_trim * self.joystick_trim_follow_gain_physical_y, -1, 1)
                virtual_stick_y_offs = elev_trim - (elev_trim * self.joystick_trim_follow_gain_virtual_y)

                phys_stick_y_offs = int(elev_trim*4096)

                if self.ap_following and ap_active:
                    input_data = HapticEffect.device.get_input()
                    phys_x, phys_y = input_data.axisXY()
                    if self._sim_is_msfs():
                        aileron_pos = telem_data.get("AileronDeflPctLR", (0, 0))
                        elevator_pos = telem_data.get("ElevDeflPct", 0)
                        aileron_pos = aileron_pos[0]
                        aileron_pos = self.dampener.dampen_value(aileron_pos, '_aileron_pos', derivative_hz=5, derivative_k=0.15)

                    if self._sim_is_xplane():
                        aileron_pos = telem_data.get("APRollServo", 0)
                        aileron_pos = self.dampener.dampen_value(aileron_pos, '_aileron_pos', derivative_hz=5, derivative_k=0.15)
                        elevator_pos = telem_data.get("APPitchServo", 0)
                        phys_stick_y_offs = int(elevator_pos*4096)


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

            if self.telemffb_controls_axes and not self.local_disable_axis_control:
                input_data = HapticEffect.device.get_input()
                phys_x, phys_y = input_data.axisXY()
                telem_data['phys_x'] = phys_x
                telem_data['phys_y'] = phys_y
                x_pos = phys_x - virtual_stick_x_offs
                y_pos = phys_y - virtual_stick_y_offs

                x_scale = clamp(self.joystick_x_axis_scale, 0, 1)
                y_scale = clamp(self.joystick_y_axis_scale, 0, 1)
                if self._sim_is_xplane():
                    pos_x_pos = x_pos * x_scale
                    pos_y_pos = y_pos * y_scale
                    self.send_xp_command(f'AXIS:jx={round(pos_x_pos, 5)},jy={round(pos_y_pos, 5)}')

                if self._sim_is_msfs():
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

            self._spring_handle.setCondition(self.spring_y)
            self._spring_handle.setCondition(self.spring_x)

        elif ffb_type == "pedals":
            if self.trim_following and self.telemffb_controls_axes and not self.local_disable_axis_control:

                rudder_trim = telem_data.get("RudderTrimPct", 0)

                rudder_trim = clamp(rudder_trim * self.rudder_trim_follow_gain_physical_x, -1, 1)
                virtual_rudder_x_offs = rudder_trim - (rudder_trim * self.rudder_trim_follow_gain_virtual_x)

                phys_rudder_x_offs = int(rudder_trim * 4096)

                if self.ap_following and ap_active:
                    input_data = HapticEffect.device.get_input()
                    # print("I am here")
                    phys_x, phys_y = input_data.axisXY()
                    if self._sim_is_msfs():
                        rudder_pos = telem_data.get("RudderDeflPct", 0)
                    if self._sim_is_xplane():
                        rudder_pos = telem_data.get("APYawServo", 0)

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

            if self.telemffb_controls_axes and not self.local_disable_axis_control:
                input_data = HapticEffect.device.get_input()
                phys_x, phys_y = input_data.axisXY()
                telem_data['phys_x'] = phys_x

                x_pos = phys_x - virtual_rudder_x_offs
                x_scale = clamp(self.rudder_x_axis_scale, 0, 1)

                if self._sim_is_xplane():
                    pos_x_pos = x_pos * x_scale
                    self.send_xp_command(f'AXIS:px={round(pos_x_pos, 5)} ')

                if self._sim_is_msfs():
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

            self._spring_handle.setCondition(self.spring_x)
        self._spring_handle.start()

    def _update_flight_controls(self, telem_data):
        # calculations loosely based on FLightGear FFB page:
        # https://wiki.flightgear.org/Force_feedback
        # https://github.com/viktorradnai/fg-haptic/blob/master/force-feedback.nas
        self._spring_handle.name = "dynamic_spring"
        if self._sim_is_msfs():
            ap_active = telem_data.get("APMaster", 0)
        if self._sim_is_xplane():
            ap_active = telem_data.get("APServos", 0)


        elev_base_gain = 0
        ailer_base_gain = 0
        rudder_base_gain = 0
        ffb_type = telem_data.get("FFBType", "joystick")
        if ffb_type == "collective":
            return

        if self.aircraft_is_fbw or telem_data.get("ACisFBW", 0):
            logging.debug ("FBW Setting enabled, running fbw_flight_controls")
            self._update_fbw_flight_controls(telem_data)
            return

        if telem_data.get("AircraftClass") == "Helicopter":
            logging.debug("Aircraft is Helicopter, aborting update_flight_controls")
            return

        if self.telemffb_controls_axes and self.ap_following and ap_active and self.use_fbw_for_ap_follow:
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
        
        incidence_vec = utils.Vector(telem_data["Incidence"])

        force_trim_x_offset = self.force_trim_x_offset
        force_trim_y_offset = self.force_trim_y_offset

        _airspeed = incidence_vec.z
        telem_data["TAS"] = _airspeed   # why not use simvar AIRSPEED TRUE?
        telem_data['TAS3'] = _airspeed  # what is this for?
        IAS = telem_data['IAS']
        telem_data['TAS_kt'] = _airspeed * ms2kt
        telem_data['IAS_kt'] = IAS * ms2kt

        base_elev_coeff = round(clamp((elev_base_gain * 4096), 0, 4096))
        base_ailer_coeff = round(clamp((ailer_base_gain * 4096), 0, 4096))
        base_rudder_coeff = round(clamp((rudder_base_gain * 4096), 0, 4096))

        #logging.info(f"Base Elev/Ailer coeff = {base_elev_coeff}/{base_ailer_coeff}")

        rudder_angle = telem_data["RudderDefl"] * rad  # + trim?
        if self._sim_is_xplane():
            rudder_angle = -rudder_angle

        # print(data["ElevDefl"] / data["ElevDeflPct"] * 100)

        slip_angle = atan2(incidence_vec.x, incidence_vec.z)
        telem_data["SideSlip"] = slip_angle*deg # overwrite sideslip with our calculated version (including wind)

        g_force = telem_data["G"] # this includes earths gravity

        _aoa = -atan2(incidence_vec.y, incidence_vec.z)*deg
        telem_data["AoA"] = _aoa

        # calculate air flow velocity exiting the prop
        # based on https://www.grc.nasa.gov/www/k-12/airplane/propth.html
        _prop_thrust = telem_data.get('PropThrust', 0)
        if isinstance(_prop_thrust, list):
            _prop_thrust = max(_prop_thrust)

        if _prop_thrust < 0:
            _prop_thrust = 0

        _prop_air_vel = sqrt(2 * _prop_thrust / (telem_data["AirDensity"] * (math.pi * (self.prop_diameter / 2) ** 2)) + _airspeed ** 2)

        telem_data['_prop_thrust'] = _prop_thrust

        if abs(incidence_vec.y) > 0.5 or _prop_air_vel > 1: # avoid edge cases
            _elevator_aoa = atan2(-incidence_vec.y, _prop_air_vel) * deg
        else:
            _elevator_aoa = 0
        telem_data["_elevator_aoa"] = _elevator_aoa


        # calculate dynamic pressure based on air flow from propeller
        # elevator_prop_flow_ratio defines how much prop wash the elevator receives
        _elev_dyn_pressure = utils.mix(telem_data["DynPressure"], 
                                       0.5 * telem_data["AirDensity"] * _prop_air_vel ** 2, 
                                       self.elevator_prop_flow_ratio) * self.dyn_pressure_scale

        # scale dynamic pressure to FFB friendly values
        _dyn_pressure = telem_data["DynPressure"] * self.dyn_pressure_scale

        # determine standard Q with Vne to get proper gain

        if self.vne_override == 0:
            if telem_data['src'] == 'XPLANE':
                vne = telem_data.get('Vne')
                vs0 = telem_data.get('Vso')
            else:
                vc, vs0, vs1 = telem_data.get("DesignSpeed")  # m/s   Vc is TAS!!
                telem_data['Vc_kt'] = vc * ms2kt
                Tvne = vc * 1.4  # rough estimate that Vne is 1.4x Vc
                # correction from TAS to IAS
                # https://aviation.stackexchange.com/questions/25801/how-do-you-convert-true-airspeed-to-indicated-airspeed
                qv= (0.5 * std_air_pressure * (Tvne ** 2))
                kmNs = ((( qv / P0) + 1) ** (2/7))
                vne = vsound * sqrt(5 * ( kmNs - 1))
        else:
            vne = self.vne_override

        telem_data['Vne_kt'] = vne * ms2kt

        Qvne = 0.5 * std_air_pressure * vne ** 2
        #Qvc = 0.5 * std_air_pressure * (vne/1.4) ** 2
        telem_data['Qvne'] = Qvne * self.dyn_pressure_scale

        Q_gain = 1/(Qvne * self.dyn_pressure_scale)
        telem_data['Qvc_gain'] = Q_gain

        self.elevator_gain = Q_gain
        self.aileron_gain = Q_gain
        self.rudder_gain = Q_gain


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

        # apply expo curve
        elevator_coeff = self.expocurve(elevator_coeff, self.elevator_expo)
        aileron_coeff = self.expocurve(aileron_coeff, self.aileron_expo)

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

        # apply expo curve
        rudder_coeff = self.expocurve(rudder_coeff, self.rudder_expo)

        telem_data["_rud_coeff"] = rudder_coeff
        rud = (slip_angle - rudder_angle) * _dyn_pressure * _slip_gain
        rud_force = clamp((rud * self.rudder_gain), -1, 1)
        rud_force = self.dampener.dampen_value(rud_force, '_rud_force', derivative_hz=5, derivative_k=.015)

        if ffb_type == 'joystick':

            if self.trim_following and self.telemffb_controls_axes and not self.local_disable_axis_control:

                elev_trim = telem_data.get("ElevTrimPct", 0)
                aileron_trim = telem_data.get("AileronTrimPct", 0)

                aileron_trim = clamp(aileron_trim * self.joystick_trim_follow_gain_physical_x, -1, 1)
                virtual_stick_x_offs = aileron_trim - (aileron_trim * self.joystick_trim_follow_gain_virtual_x)

                elev_trim = clamp(elev_trim * self.joystick_trim_follow_gain_physical_y, -1, 1)

                elev_trim = self.dampener.dampen_value(elev_trim, '_elev_trim', derivative_hz=5, derivative_k=0.15)

                virtual_stick_y_offs = elev_trim - (elev_trim * self.joystick_trim_follow_gain_virtual_y)
                phys_stick_y_offs = int(elev_trim * 4096)


                if self.ap_following and ap_active:
                    if self._sim_is_msfs():
                        aileron_pos = telem_data.get("AileronDeflPctLR", (0, 0))
                        aileron_pos = aileron_pos[0]
                        aileron_pos = self.dampener.dampen_value(aileron_pos, '_aileron_pos', derivative_hz=5, derivative_k=0.15)
                    if self._sim_is_xplane():
                        aileron_pos = telem_data.get("APRollServo", 0)


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
                telem_data['phys_x'] = phys_x
                telem_data['phys_y'] = phys_y

                x_pos = phys_x - virtual_stick_x_offs
                y_pos = phys_y - virtual_stick_y_offs

                x_scale = clamp(self.joystick_x_axis_scale, 0, 1)
                y_scale = clamp(self.joystick_y_axis_scale, 0, 1)
                if self._sim_is_xplane():
                    pos_x_pos = x_pos * x_scale
                    pos_y_pos = y_pos * y_scale
                    self.send_xp_command(f'AXIS:jx={round(pos_x_pos, 5)},jy={round(pos_y_pos, 5)}')

                if self._sim_is_msfs():
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
            if self.aoa_effect_enabled and telem_data.get("ElevDeflPct", 0) != 0 and not max(telem_data.get("WeightOnWheels")):
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

            max_coeff_y = int(4096 * self.max_elevator_coeff)
            realtime_coeff_y = int(4096 * elevator_coeff)
            ec = int(utils.scale_clamp(realtime_coeff_y, (base_elev_coeff, 4096), (base_elev_coeff, max_coeff_y)))

            pct_max_e = ec/max_coeff_y

            telem_data["_pct_max_e"] = pct_max_e
            self._ipc_telem["_pct_max_e"] = pct_max_e
            logging.debug(f"Elev Coef: {ec}")
            telem_data['_ec'] = ec

            self.spring_y.negativeCoefficient = self.spring_y.positiveCoefficient = ec


            max_coeff_x = int(4096 * self.max_aileron_coeff)
            realtime_coeff_x = int(4096 * aileron_coeff)
            ac = int(utils.scale_clamp(realtime_coeff_x, (base_ailer_coeff, 4096), (base_ailer_coeff, max_coeff_x)))

            pct_max_a = ac/max_coeff_x

            telem_data["_pct_max_a"] = pct_max_a
            self._ipc_telem["_pct_max_a"] = pct_max_a
            telem_data['_ac'] = ac
            logging.debug(f"Ailer Coef: {ac}")

            self.spring_x.positiveCoefficient = self.spring_x.negativeCoefficient = ac

            # update spring data
            self._spring_handle.setCondition(self.spring_y)
            self._spring_handle.setCondition(self.spring_x)

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

            self._spring_handle.start() # ensure spring is started

        elif ffb_type == 'pedals':
            if self.trim_following and self.telemffb_controls_axes and not self.local_disable_axis_control:

                rudder_trim = telem_data.get("RudderTrimPct", 0)

                rudder_trim = clamp(rudder_trim * self.rudder_trim_follow_gain_physical_x, -1, 1)
                virtual_rudder_x_offs = rudder_trim - (rudder_trim * self.rudder_trim_follow_gain_virtual_x)

                phys_rudder_x_offs = int(rudder_trim * 4096)
            else:
                phys_rudder_x_offs = 0
                virtual_rudder_x_offs = 0

            max_coeff_x = int(4096*self.max_rudder_coeff)
            realtime_coeff_x = int(4096 * rudder_coeff)
            rc = int(utils.scale_clamp(realtime_coeff_x, (base_rudder_coeff, 4096), (base_rudder_coeff, max_coeff_x)))

            pct_max_r = rc/max_coeff_x

            telem_data["_pct_max_r"] = pct_max_r
            self._ipc_telem["_pct_max_r"] = pct_max_r
            telem_data['_rc'] = rc
            self.spring_x.negativeCoefficient = self.spring_x.positiveCoefficient = rc
            self.spring_x.cpOffset = phys_rudder_x_offs

            self._spring_handle.setCondition(self.spring_x)

            speed_factor = utils.scale_clamp(IAS, (0, vne), (0.0, 1.0))
            rud_force = rud_force * speed_factor
            # telem_data["RudForce"] = rud_force * speed_factor

            if self.telemffb_controls_axes and not self.local_disable_axis_control:
                input_data = HapticEffect.device.get_input()
                phys_x, phys_y = input_data.axisXY()
                telem_data['phys_x'] = phys_x
                x_pos = phys_x - virtual_rudder_x_offs
                x_scale = clamp(self.rudder_x_axis_scale, 0, 1)

                if self._sim_is_xplane():
                    pos_x_pos = x_pos * x_scale
                    self.send_xp_command(f'AXIS:px={round(pos_x_pos, 5)}')

                if self._sim_is_msfs():
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
            self._spring_handle.start()
            
    def _update_stick_shaker(self, telem_data):
        if not self._sim_is_msfs():
            return

        if not self.enable_stick_shaker:
            effects['stick_shaker'].destroy()
            return

        stall = telem_data.get('StallWarning', 0)
        if stall:
            effects['stick_shaker'].periodic(14, self.stick_shaker_intensity, 0, EFFECT_SQUARE).start()
        else:
            effects['stick_shaker'].destroy()


    def send_xp_command(self, cmd):
        if not getattr(self, "_socket", None):
            self._socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, 0)
        self.toggle_xp_control()
        self._socket.sendto(bytes(cmd, "utf-8"), ("127.0.0.1", 34391))

    def toggle_xp_control(self):
        if self.telem_data.get('FFBType', '') == 'collective':
            # issues with axis override for collectve
            return

        if not getattr(self, "_socket", None):
            self._socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, 0)

        if self.telem_data.get('FFBType', '') == 'collective' and self.telem_data.get("AircraftClass", '') != "Helicopter":
            # we don't want to send the "prop pitch" override (collective) to XPLANE if we are not in a helo
            if self.telem_data.get("cOvrd", 1):
                sendstr = f"OVERRIDE:{self.telem_data['FFBType']}=false"
                self._socket.sendto(bytes(sendstr, "utf-8"), ("127.0.0.1", 34391))
                logging.info(f"Sending to XPLANE: >>{sendstr}<<")
                self.xplane_axis_override_active = False
            return

        if self.telemffb_controls_axes and not self.local_disable_axis_control and not self.xplane_axis_override_active:
            sendstr = f"OVERRIDE:{self.telem_data['FFBType']}=true"
            self._socket.sendto(bytes(sendstr, "utf-8"), ("127.0.0.1", 34391))
            logging.info(f"Sending to XPLANE: >>{sendstr}<<")
            self.xplane_axis_override_active = True
        elif self.xplane_axis_override_active and not self.telemffb_controls_axes:
            sendstr = f"OVERRIDE:{self.telem_data['FFBType']}=false"
            self._socket.sendto(bytes(sendstr, "utf-8"), ("127.0.0.1", 34391))
            logging.info(f"Sending to XPLANE: >>{sendstr}<<")
            self.xplane_axis_override_active = False

    def _override_collective_spring(self):
        """
        Method specifically intended to start a spring with force=0 for use in fixed wing aircraft so it may be stowed
        and kept out of the way
        .
        Option to leave spring active also exists
        """
        if not self.is_collective(): return

        self.spring = effects["collective_ap_spring"].spring()

        if not self.force_disable_collective_gain:
            self.spring_y.negativeCoefficient = self.spring_y.positiveCoefficient = 4096
            self.spring_y.cpOffset = 0
            self.spring.setCondition(self.spring_y)
            self.spring.start(override=True)
            return

        self.spring_y.negativeCoefficient = self.spring_y.positiveCoefficient = 0
        self.spring.setCondition(self.spring_y)
        self.spring.start(override=True)

    def find_xp_gear_orientation(self, x, y, z):
        pass

    @overrides(AircraftBase)
    def on_event(self, event, *args):
        logging.info(f"on_event {event} {args}")

        if event == "STOP":
            self.on_timeout()

    @overrides(AircraftBase)
    def on_telemetry(self, telem_data):
        effects["pause_spring"].destroy()

        if telem_data.get('Parked', 0): # MSFS in Hangar
            return

        if self._sim_is_xplane():
            self.toggle_xp_control()

        super().on_telemetry(telem_data)

        if telem_data['src'] == "XPLANE":
            incidence_vec = utils.Vector(telem_data["VelAcf"])
        else:
            incidence_vec = utils.Vector(telem_data["VelWorld"])
            wind_vec = utils.Vector(telem_data["AmbWind"])
            incidence_vec = incidence_vec - wind_vec
            # Rotate the vector from world frame into body frame
            incidence_vec = incidence_vec.rotY(-(telem_data["Heading"] * rad))
            incidence_vec = incidence_vec.rotX(-telem_data["Pitch"] * rad)
            incidence_vec = incidence_vec.rotZ(-telem_data["Roll"] * rad)

        telem_data["Incidence"] = list(incidence_vec)

        #
        ### Generic Aircraft Class Telemetry Handler
        if not "AircraftClass" in telem_data:
            telem_data["AircraftClass"] = "GenericAircraft"  # inject aircraft class into telemetry

        hyd_loss = self._update_hydraulic_loss_effect(telem_data)
        if not hyd_loss: self._update_ffb_forces(telem_data)
        self._update_stick_shaker(telem_data)
        self._update_runway_rumble(telem_data)
        self._update_buffeting(telem_data)
        self._update_flight_controls(telem_data)
        self._decel_effect(telem_data)
        self._update_touchdown_effect(telem_data)
        if self._sim_is_xplane():
            self._update_canopy(telem_data.get("CanopyPos", 0))
        #
        if self.flaps_motion_intensity > 0:
            flps = telem_data.get("Flaps", 0)
            if isinstance(flps, list):
                flps = max(flps)

            self._update_flaps(flps)
        retracts = telem_data.get("RetractableGear", 0)
        if isinstance(retracts, list):
            retracts = max(retracts)
        if (self.gear_motion_intensity > 0) and (retracts):
            gear = max(telem_data.get("Gear", 0))
            self._update_landing_gear(gear, telem_data.get("IAS"))

        self._aoa_reduction_force_effect(telem_data)
        if self._sim_is_msfs():
            if self.nosewheel_shimmy and telem_data.get("FFBType") == "pedals" and not telem_data.get("IsTaildragger", 0):
                self._update_nosewheel_shimmy(telem_data)

    def on_timeout(self):
        if not effects["pause_spring"].started:
            super().on_timeout()

        self.cyclic_spring_init = 0

        self.const_force.stop()
        self._spring_handle.stop()
        if self.center_spring_on_pause:
            self.spring_x.negativeCoefficient = self.spring_x.positiveCoefficient = 4096
            self.spring_y.negativeCoefficient = self.spring_y.positiveCoefficient = 4096
            self.spring_x.cpOffset = self.spring_y.cpOffset = 0
            
            pause_spring = effects["pause_spring"].spring()
            pause_spring.setCondition(self.spring_x)
            pause_spring.setCondition(self.spring_y)
            pause_spring.start()




class PropellerAircraft(Aircraft):
    """Generic Class for Prop aircraft"""
    def __init__(self, name, **kwargs):
        super().__init__(name, **kwargs)

    # run on every telemetry frame
    @overrides(Aircraft)
    def on_telemetry(self, telem_data):
        ### Propeller Aircraft Class Telemetry Handler
        if telem_data.get("N") == None:
            return
        telem_data["AircraftClass"] = "PropellerAircraft"  # inject aircraft class into telemetry

        super().on_telemetry(telem_data)

        self.update_piston_engine_rumble(telem_data)
        if self._sim_is_msfs():
            if self.spoiler_motion_intensity > 0 or self.spoiler_buffet_intensity > 0:
                sp = max(telem_data.get("Spoilers", 0))
                self._update_spoiler(sp, telem_data.get("IAS"), spd_thresh_low=80*kt2ms, spd_thresh_hi=140*kt2ms )
        if self._sim_is_xplane():
            self._update_speed_brakes(telem_data.get("SpeedbrakePos", 0), telem_data.get("IAS"), spd_thresh=80 * kt2ms)
        self.new_gforce_effect(telem_data)
        if self.is_collective():
            self._override_collective_spring()

class JetAircraft(Aircraft):
    """Generic Class for Jets"""
    # flaps_motion_intensity = 0.0

    _ab_is_playing = 0

    def __init__(self, name, **kwargs):
        super().__init__(name, **kwargs)
    # run on every telemetry frame

    @overrides(Aircraft)
    def on_telemetry(self, telem_data):
        ## Jet Aircraft Telemetry Manager
        if telem_data.get("N") == None:
            return
        
        telem_data["AircraftClass"] = "JetAircraft"  # inject aircraft class into telemetry

        super().on_telemetry(telem_data)
        #
        if self._sim_is_xplane():
            self._update_speed_brakes(telem_data.get("SpeedbrakePos", 0), telem_data.get("IAS"), spd_thresh=150*kt2ms)
        if self._sim_is_msfs():
            if self.spoiler_motion_intensity > 0 or self.spoiler_buffet_intensity > 0:
                sp = max(telem_data.get("Spoilers", 0))
                self._update_spoiler(sp, telem_data.get("IAS"), spd_thresh_low=150*kt2ms, spd_thresh_hi=300*kt2ms )
        self._update_jet_engine_rumble(telem_data)
        self.new_gforce_effect(telem_data)
        self._update_ab_effect(telem_data)
        if self.is_collective():
            self._override_collective_spring()
class TurbopropAircraft(PropellerAircraft):
    def __init__(self, name, **kwargs):
        super().__init__(name, **kwargs)

    @overrides(PropellerAircraft)
    def on_telemetry(self, telem_data):
        pass
        if telem_data.get("N") == None:
            return
        telem_data["AircraftClass"] = "TurbopropAircraft"  # inject aircraft class into telemetry

        super().on_telemetry(telem_data)
        if self._sim_is_xplane():
            self._update_speed_brakes(telem_data.get("SpeedbrakePos", 0), telem_data.get("IAS"), spd_thresh=120*kt2ms)
        if self._sim_is_msfs():
            if self.spoiler_motion_intensity > 0 or self.spoiler_buffet_intensity > 0:
                sp = max(telem_data.get("Spoilers", 0))
                self._update_spoiler(sp, telem_data.get("IAS"), spd_thresh_low=120*kt2ms, spd_thresh_hi=260*kt2ms )
        self.new_gforce_effect(telem_data)

        self._update_jet_engine_rumble(telem_data)
        if self.is_collective():
            self._override_collective_spring()
class GliderAircraft(Aircraft):
    def __init__(self, name, **kwargs):
        super().__init__(name, **kwargs)

    def _update_force_trim(self, telem_data, x_axis=True, y_axis=True):
        if not self.force_trim_enabled: 
            return
        
        ffb_type = telem_data.get("FFBType", "joystick")
        offs_x = 0
        offs_y = 0
        if ffb_type != "joystick":
            return
        if self.force_trim_button == 0:
            self.flag_error("Force trim enabled but buttons not configured")
            return

        self._spring_handle.name = "dynamic_spring"
        # logging.debug(f"update_force_trim: x={x_axis}, y={y_axis}")
        input_data = HapticEffect.device.get_input()

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

                self._spring_handle.setCondition(self.spring_x)

            if y_axis:
                self.spring_y.positiveCoefficient = 2048
                self.spring_y.negativeCoefficient = 2048

                offs_y = round(y * 4096)
                self.spring_y.cpOffset = offs_y

                self._spring_handle.setCondition(self.spring_y)

            self.stick_center = [x,y]

            logging.debug(f"Force Trim Disengaged:{round(x * 4096)}:{round(y * 4096)}")

            self.force_trim_release_active = 1

        if not force_trim_pressed and self.force_trim_release_active:

            self.spring_x.positiveCoefficient = clamp(int(4096 * self.aileron_spring_gain), 0, 4096)
            self.spring_x.negativeCoefficient = self.spring_x.positiveCoefficient

            self.spring_y.positiveCoefficient = clamp(int(4096 * self.elevator_spring_gain), 0, 4096)
            self.spring_y.negativeCoefficient = self.spring_y.positiveCoefficient
            if x_axis:
                offs_x = round(x * 4096)
                self.spring_x.cpOffset = offs_x
                self._spring_handle.setCondition(self.spring_x)

            if y_axis:
                offs_y = round(y * 4096)
                self.spring_y.cpOffset = offs_y
                self._spring_handle.setCondition(self.spring_y)

            # self.spring.start()
            self.stick_center = [x,y]

            logging.debug(f"Force Trim Engaged :{offs_x}:{offs_y}")

            self.force_trim_release_active = 0

        if trim_reset_pressed or not self.force_trim_spring_init:
            if trim_reset_pressed:
                self.stick_center = [0, 0]

            if x_axis:
                self.spring_x.positiveCoefficient = clamp(int(4096 * self.aileron_spring_gain), 0, 4096)
                self.spring_x.negativeCoefficient = self.spring_x.positiveCoefficient
                cpO_x = round(self.cyclic_center[0]*4096)
                self.spring_x.cpOffset = cpO_x
                self._spring_handle.setCondition(self.spring_x)

            if y_axis:
                self.spring_y.positiveCoefficient = clamp(int(4096 * self.elevator_spring_gain), 0, 4096)
                self.spring_y.negativeCoefficient = self.spring_y.positiveCoefficient
                cpO_y = round(self.cyclic_center[1]*4096)
                self.spring_y.cpOffset = cpO_y
                self._spring_handle.setCondition(self.spring_y)

            # self.spring.start()
            self.force_trim_spring_init = 1
            logging.info("Trim Reset Pressed")
            return

        telem_data["StickXY"] = [x, y]
        telem_data["StickXY_offset"] = self.stick_center
        self.force_trim_x_offset = self.stick_center[0]
        self.force_trim_y_offset = self.stick_center[1]

    @overrides(Aircraft)
    def on_telemetry(self, telem_data):
        pass
        if telem_data.get("N") == None:
            return
        telem_data["AircraftClass"] = "GliderAircraft"  # inject aircraft class into telemetry

        super().on_telemetry(telem_data)
        if self.force_trim_enabled:
            self._update_force_trim(telem_data, x_axis=self.aileron_force_trim, y_axis=self.elevator_force_trim)
        if self._sim_is_msfs():
            sp = max(telem_data.get("Spoilers", 0))
        if self._sim_is_xplane():
            sp = telem_data.get("SpeedbrakePos", 0)
            if isinstance(sp, list):
                sp = max(sp)
        if self.spoiler_motion_intensity > 0 or self.spoiler_buffet_intensity > 0:
            self._update_spoiler(sp, telem_data.get("IAS"), spd_thresh_low=60*kt2ms, spd_thresh_hi=120*kt2ms )
        self.new_gforce_effect(telem_data)

        if self.is_collective():
            self._override_collective_spring()
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
    last_pos_y_pos = 0
    last_pos_x_pos = 0
    last_collective_y = 1
    last_pedal_x = 0
    collective_init = 0
    collective_ap_spring_gain = 1
    collective_dampening_gain = 0
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
    
    @overrides(Aircraft)
    def on_timeout(self):
        super().on_timeout()
        self.cyclic_spring_init = 0
        self.collective_init = 0
        self.pedals_init = 0

    @overrides(Aircraft)
    def on_telemetry(self, telem_data):
        self.speedbrake_motion_intensity = 0.0
        if telem_data.get("N") == None:
            return
        telem_data["AircraftClass"] = "Helicopter"  # inject aircraft class into telemetry

        super().on_telemetry(telem_data)

        self._update_heli_controls(telem_data)
        self._update_collective(telem_data)
        # # self._update_cyclic_trim(telem_data)
        self._update_pedals(telem_data)
        self._calc_etl_effect(telem_data, blade_ct=self.rotor_blade_count)
        self._update_jet_engine_rumble(telem_data)
        self._update_heli_engine_rumble(telem_data, blade_ct=self.rotor_blade_count)
        self._update_vrs_effect(telem_data)

    # def step_value_over_time(self, key, value, timeframe_ms, dst_val):
    #     current_time_ms = time.time() * 1000  # Convert current time to milliseconds
    #
    #     if key not in self.stepper_dict:
    #         self.stepper_dict[key] = {'value': value, 'dst_val': dst_val, 'start_time': current_time_ms, 'timeframe': timeframe_ms}
    #
    #     data = self.stepper_dict[key]
    #
    #     if data['value'] == data['dst_val']:
    #         del self.stepper_dict[key]
    #         return data['value']
    #
    #     elapsed_time_ms = current_time_ms - data['start_time']
    #
    #     if elapsed_time_ms >= timeframe_ms:
    #         data['value'] = data['dst_val']
    #         return data['dst_val']
    #
    #     remaining_time_ms = timeframe_ms - elapsed_time_ms
    #     step_size = (data['dst_val'] - value) / remaining_time_ms
    #
    #     data['value'] = round(value + step_size * elapsed_time_ms)
    #     # print(f"value out = {data['value']}")
    #     return data['value']

    def _update_heli_controls(self, telem_data):
        ffb_type = telem_data.get("FFBType", "joystick")
        if self._sim_is_msfs():
            ap_active = telem_data.get("APMaster", 0)
        if self._sim_is_xplane():
            ap_active = telem_data.get("APServos", 0)

        # trim_reset = max(telem_data.get("h145TrimRelease", 0), telem_data.get("h160TrimRelease", 0))
        self._spring_handle.name = "cyclic_spring"
        force_trim_active = telem_data.get('ForceTrimSW', True)  # Enable cockpit switch control (if exists) for force trim.  Add LVar as "ForceTrimSW" bool if available for aircraft
        if ffb_type == "joystick":
            if self.force_trim_enabled and force_trim_active:

                if self.force_trim_button == 0:
                    self.flag_error("Force trim enabled but buttons not configured")
                    return
                input_data = HapticEffect.device.get_input()

                force_trim_pressed = input_data.isButtonPressed(self.force_trim_button)
                if self.force_trim_reset_button > 0:
                    trim_reset_pressed = input_data.isButtonPressed(self.force_trim_reset_button)
                else:
                    trim_reset_pressed = False
                x, y = input_data.axisXY()
                telem_data['phys_x'] = x
                telem_data['phys_y'] = y
                if force_trim_pressed:
                    gain = int(self.trim_release_spring_gain * 4096)
                    self.spring_x.positiveCoefficient = gain
                    self.spring_x.negativeCoefficient = gain

                    self.spring_y.positiveCoefficient = gain
                    self.spring_y.negativeCoefficient = gain

                    self.cpO_x = round(x * 4096)
                    self.spring_x.cpOffset = self.cpO_x

                    self.cpO_y = round(y * 4096)
                    self.spring_y.cpOffset = self.cpO_y

                    self.cyclic_center = [x, y]

                    logging.info(f"Force Trim Disengaged:{round(x * 4096)}:{round(y * 4096)}, gain:{gain}")

                    self.cyclic_trim_release_active = 1
                    if self._sim_is_msfs():
                        self._simconnect.send_event_to_msfs("ROTOR_TRIM_RESET", 1)

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

                    logging.debug(f"Force Trim Engaged :{self.cpO_x}:{self.cpO_y}")
                    if self._sim_is_msfs():
                        self._simconnect.send_event_to_msfs("ROTOR_TRIM_RESET", 0)


                    self.cyclic_trim_release_active = 0

                elif trim_reset_pressed or not self.trim_reset_complete:
                    self.cpO_x = self.step_value_over_time("center_x", self.cpO_x, 1000, 0)
                    self.cpO_y = self.step_value_over_time("center_y", self.cpO_y, 1000, 0)

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
                    if self._sim_is_msfs():
                        self._simconnect.send_event_to_msfs("ROTOR_TRIM_RESET", 0)

                    logging.info("Trim Reset Pressed")

                elif not self.cyclic_spring_init:
                    self.cyclic_center = [0, 0]

                    input_data = HapticEffect.device.get_input()


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
                        self.last_pos_x_pos = 0
                        self.last_pos_y_pos = 0
                    else:
                        self.cpO_x = round(self.last_device_x * 4096)
                        self.cpO_y = round(self.last_device_y * 4096)

                    self.spring_x.cpOffset = self.cpO_x
                    self.spring_y.cpOffset = self.cpO_y
                    self._spring_handle.setCondition(self.spring_x)
                    self._spring_handle.setCondition(self.spring_y)
                    self._spring_handle.start()
                    if (self.cpO_x/4096 - 0.15 < phys_x < self.cpO_x/4096 + 0.15) and (self.cpO_y/4096 - 0.15 < phys_y < self.cpO_y/4096 + 0.15):
                        #dont start sending position until physical stick has centered
                        self.cyclic_spring_init = 1
                        logging.info("Cyclic Spring Initialized")
                    else:
                        if self._sim_is_msfs():
                            if self.enable_custom_x_axis:
                                x_var = self.custom_x_axis
                            else:
                                x_var = 'AXIS_CYCLIC_LATERAL_SET'
                            if self.enable_custom_y_axis:
                                y_var = self.custom_y_axis
                            else:
                                y_var = 'AXIS_CYCLIC_LONGITUDINAL_SET'

                            self._simconnect.send_event_to_msfs(x_var, self.last_pos_x_pos)
                            self._simconnect.send_event_to_msfs(y_var, self.last_pos_y_pos)
                        return

                telem_data["StickXY"] = [x, y]
                telem_data["StickXY_offset"] = self.cyclic_center
            else:
                self._spring_handle.stop()

            if self.telemffb_controls_axes and not self.local_disable_axis_control:
                input_data = HapticEffect.device.get_input()
                phys_x, phys_y = input_data.axisXY()
                telem_data['phys_x'] = phys_x
                telem_data['phys_y'] = phys_y
                self._update_cyclic_trim(telem_data)

                x_pos = phys_x - self.cyclic_virtual_trim_x_offs
                y_pos = phys_y - self.cyclic_virtual_trim_y_offs

                x_scale = clamp(self.joystick_x_axis_scale, 0, 1)
                y_scale = clamp(self.joystick_y_axis_scale, 0, 1)

                if self._sim_is_xplane():
                    pos_x_pos = x_pos * x_scale
                    pos_y_pos = y_pos * y_scale
                    self.send_xp_command(f'AXIS:jx={round(pos_x_pos, 5)},jy={round(pos_y_pos, 5)}')

                if self.cyclic_spring_init or not (self.force_trim_enabled and force_trim_active):
                    if self._sim_is_msfs():
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
                        self.last_pos_x_pos = pos_x_pos
                        self.last_pos_y_pos = pos_y_pos

                self.last_device_x, self.last_device_y = phys_x, phys_y

            if self.anything_has_changed("cyclic_gain", self.cyclic_spring_gain):  # check if spring gain setting has been modified in real time
                self.spring_x.positiveCoefficient = clamp(int(4096 * self.cyclic_spring_gain), 0, 4096)
                self.spring_x.negativeCoefficient = self.spring_x.positiveCoefficient

                self.spring_y.positiveCoefficient = clamp(int(4096 * self.cyclic_spring_gain), 0, 4096)
                self.spring_y.negativeCoefficient = self.spring_y.positiveCoefficient

            self.spring_x.cpOffset = int(self.cpO_x) + self.cyclic_physical_trim_x_offs
            self.spring_y.cpOffset = int(self.cpO_y) + self.cyclic_physical_trim_y_offs
            self._spring_handle.setCondition(self.spring_x)
            self._spring_handle.setCondition(self.spring_y)
            if self.force_trim_enabled and force_trim_active:
                self._spring_handle.start()

    def _update_cyclic_trim(self, telem_data):
        if telem_data.get("FFBType", None) != 'joystick':
            return
        if not self.trim_following:
            return
        if self._sim_is_msfs():
            cyclic_x_trim = telem_data.get("CyclicTrimX", 0)
            cyclic_y_trim = telem_data.get("CyclicTrimY", 0)
        if self._sim_is_xplane():
            cyclic_x_trim = telem_data.get("AileronTrimPct", 0)
            cyclic_y_trim = telem_data.get("ElevTrimPct", 0)


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

        if self.telemffb_controls_axes and not self.local_disable_axis_control:
            input_data = HapticEffect.device.get_input()
            phys_x, phys_y = input_data.axisXY()
            x_scale = clamp(self.rudder_x_axis_scale, 0, 1)

            self._spring_handle.name = "pedal_ap_spring"
            # self.damper = effects["pedal_damper"].damper()

            pedal_pos = telem_data.get("TailRotorPedalPos")
            input_data = HapticEffect.device.get_input()
            phys_x, phys_y = input_data.axisXY()
            telem_data['phys_x'] = phys_x
            if not self.pedals_init:

                self.spring_x.negativeCoefficient = self.spring_x.positiveCoefficient = self.pedal_spring_coeff_x
                if telem_data.get("SimOnGround", 1):
                    self.cpO_x = 0
                    self.last_pos_x_pos = 0
                else:
                    # print(f"last_colelctive_y={self.last_collective_y}")
                    self.cpO_x = round(4096 * self.last_pedal_x)

                self.spring_x.positiveCoefficient = self.spring_x.negativeCoefficient = round(4096 * utils.clamp(self.pedal_spring_gain, 0, 1))

                self.spring_x.cpOffset = self.cpO_x

                self._spring_handle.setCondition(self.spring_x)
                # self.damper.damper(coef_x=int(4096 * self.pedal_dampening_gain)).start()
                self._spring_handle.start()
                logging.debug(f"self.cpO_x:{self.cpO_x}, phys_x:{phys_x}")
                if self.cpO_x / 4096 - 0.1 < phys_x < self.cpO_x / 4096 + 0.1:
                    # dont start sending position until physical pedals have centered
                    self.pedals_init = 1
                    logging.info("Pedals Initialized")
                    if not self.pedal_force_trim_enabled:
                        self._spring_handle.stop()
                else:
                    if self._sim_is_msfs():
                        if self.enable_custom_x_axis:
                            x_var = self.custom_x_axis
                        else:
                            x_var = 'ROTOR_AXIS_TAIL_ROTOR_SET'

                        self._simconnect.send_event_to_msfs(x_var, self.last_pos_x_pos)
                    return

            if self.pedal_force_trim_enabled:
                if not self._spring_handle.started:
                    self._spring_handle.start()

                if not self._update_pedal_force_trim(telem_data):
                    spring_coeff = round(utils.clamp((self.pedal_spring_gain * 4096), 0, 4096))
                    self.spring_x.positiveCoefficient = self.spring_x.negativeCoefficient = spring_coeff

                self._spring_handle.setCondition(self.spring_x)
            else:
                if self._spring_handle.started:
                    self._spring_handle.stop()


            self.last_pedal_x = phys_x

            if self._sim_is_xplane():
                pos_x_pos = phys_x * x_scale
                self.send_xp_command(f'AXIS:px={round(pos_x_pos, 5)}')
            if self._sim_is_msfs():
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
                self.last_pos_x_pos = pos_x_pos

    def _update_collective(self, telem_data):
        if telem_data.get("FFBType") != 'collective':
            return
        if not self.telemffb_controls_axes and not self.local_disable_axis_control:
            return
        self._spring_handle.name = "collective_ap_spring"
        # self.damper = effects["collective_damper"].damper()
        # collective_tr = telem_data.get("h145CollectiveRelease", 0)
        # afcs_mode = telem_data.get("h145CollectiveAfcsMode", 0)
        collective_pos = telem_data.get("CollectivePos", 0)

        # input_data = HapticEffect.device.getInput()
        # phys_x, phys_y = input_data.axisXY()
        # SimVar("h145CollectiveRelease", "L:H145_SDK_AFCS_COLLECTIVE_TRIM_IS_RELEASED", "bool"),
        # SimVar("h145CollectiveAfcsMode", "L:H145_SDK_AFCS_MODE_COLLECTIVE", "number"),
        # SimVar("CollectivePos", "COLLECTIVE POSITION", "percent over 100"),
        input_data = HapticEffect.device.get_input()
        phys_x, phys_y = input_data.axisXY()
        telem_data['phys_y'] = phys_y
        if not self.collective_init:
            self.spring_y.negativeCoefficient = self.spring_y.positiveCoefficient = self.collective_spring_coeff_y
            if self._sim_is_msfs():
                if self.enable_custom_y_axis:
                    y_var = self.custom_y_axis
                    y_range = self.raw_y_axis_scale
                else:
                    y_var = 'AXIS_COLLECTIVE_SET'
                    y_range = 16384
            if telem_data.get("SimOnGround", 1):
                self.cpO_y = 4096
                if self._sim_is_msfs():
                    if y_range != 1:
                        self.last_pos_y_pos = -y_range * 1
                    else:
                        self.last_pos_y_pos = 1
            else:
                # print(f"last_colelctive_y={self.last_collective_y}")
                self.cpO_y = round(4096 * self.last_collective_y)

            self.spring_y.positiveCoefficient = self.spring_y.negativeCoefficient = round(
                4096 * utils.clamp(self.collective_ap_spring_gain, 0, 1))

            self.spring_y.cpOffset = self.cpO_y

            self._spring_handle.setCondition(self.spring_y)
            # self.damper.damper(coef_y=int(4096*self.collective_dampening_gain)).start()
            self._spring_handle.start()
            # print(f"self.cpO_y:{self.cpO_y}, phys_y:{phys_y}")
            if self.cpO_y/4096 - 0.1 < phys_y < self.cpO_y/4096 + 0.1:
                # dont start sending position until physical stick has centered
                self.collective_init = 1
                logging.info("Collective Initialized")
            else:
                if self._sim_is_msfs():
                    self._simconnect.send_event_to_msfs(y_var, self.last_pos_y_pos)

                return
        self.last_collective_y = phys_y
        self.cpO_y = round(4096 * utils.clamp(phys_y, -1, 1))
        # print(self.cpO_y)
        self.spring_y.cpOffset = self.cpO_y

        # self.damper.damper(coef_y=int(4096*self.collective_dampening_gain)).start()
        self.spring_y.negativeCoefficient = self.spring_y.positiveCoefficient = round(
            self.collective_spring_coeff_y / 2)

        self._spring_handle.setCondition(self.spring_y)
        self._spring_handle.start()

        if self._sim_is_xplane():
            pos_y_pos = utils.scale(phys_y, (-1, 1), (1, 0))
            if self.collective_init:
                self.send_xp_command(f'AXIS:cy={round(pos_y_pos, 5)}')

        if self._sim_is_msfs():
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
                self.last_pos_y_pos = pos_y_pos




class HPGHelicopter(Helicopter):
    sema_x_max = 5
    sema_y_max = 5
    sema_yaw_max = 5
    afcs_step_size = 2
    collective_init = 0
    collective_ap_spring_gain = 1
    collective_dampening_gain = 0
    collective_spring_coeff_y = 0
    hands_on_deadzone = 0.1
    hands_off_deadzone = 0.02
    feet_on_deadzone = 0.05
    feet_off_deadzone = 0.03
    hands_on_active = 0
    hands_on_x_active = 0
    hands_on_y_active = 0
    feet_on_active = 0
    send_individual_hands_on = 0
    vrs_effect_enable: bool = True
    vrs_effect_intensity = 0

    def __init__(self, name, **kwargs):
        super().__init__(name, **kwargs)

        input_data = HapticEffect.device.get_input()
        self.phys_x, self.phys_y = input_data.axisXY()
        self.cpO_y = round(self.phys_y * 4096)
        self.collective_spring_coeff_y = round(4096 * utils.clamp(self.collective_ap_spring_gain, 0, 1))
        self.hands_on_active = 0
        self.hands_on_x_active = 0
        self.hands_on_y_active = 0
        self.feet_on_active = 0

    def on_telemetry(self, telem_data):
        super().on_telemetry(telem_data)

        # self._update_vrs_effect(telem_data)

    def on_timeout(self):
        super().on_timeout()
        self.collective_init = 0
        self.pedals_init = 0

    def check_feet_on(self, percent):
        input_data = HapticEffect.device.get_input()
        phys_x, phys_y = input_data.axisXY()

        # Convert phys input to +/-4096
        phys_x = round(phys_x * 4096)

        ref_x = self.cpO_x

        # Calculate the threshold values based on the input percentage
        threshold = 4096 * percent

        # Calculate the deviation percentages in decimal form
        deviation_x = abs(phys_x - ref_x) / 4096

        # Check if either phys_x or phys_y exceeds the threshold
        x_exceeds_threshold = abs(phys_x - ref_x) > threshold

        result = {
            "result": x_exceeds_threshold,
            "deviation": deviation_x,
        }

        return result



    def check_hands_on(self, percent):
        input_data = HapticEffect.device.get_input()
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
        # trim_reset = max(telem_data.get("h145TrimRelease", 0), telem_data.get("h160TrimRelease", 0))
        trim_reset = telem_data.get("hpgTrimRelease", 0)

        if ffb_type == "joystick":
            if not self.telemffb_controls_axes and not self.local_disable_axis_control:
                self.flag_error(
                    "Aircraft is configured as class HPGHelicopter.  For proper integration, TelemFFB must send axis position to MSFS.\n\nPlease enable 'telemffb_controls_axes' in your config and unbind the cyclic axes in MSFS settings")
                return
            sema_x = telem_data.get("hpgSEMAx", 0)
            sema_y = telem_data.get("hpgSEMAy", 0)

            # sema_x_avg = self.smoother.get_rolling_average('s_sema_x', sema_x, window_ms=500)
            # sema_y_avg = self.smoother.get_rolling_average('s_sema_y', sema_y, window_ms=500)
            sema_x_avg = sema_x
            sema_y_avg = sema_y

            if not trim_reset:
                sx = round(abs(sema_x_avg), 3)
                sy = round(abs(sema_y_avg), 3)
                if 100 >= sx >= 50:
                    self.afcsx_step_size = 5
                elif 49.999 > sx >= 20:
                    self.afcsx_step_size = 3
                elif 19.999 > sx >= 10:
                    self.afcsx_step_size = 2
                elif 9.999 > sx >= 5:
                    self.afcsx_step_size = 1
                elif 4.999 > sx >= 0:
                    self.afcsx_step_size = 1
                else:
                    self.afcsx_step_size = 0


                if 100 >= sy >= 50:
                    self.afcsy_step_size = 5
                elif 49.999 > sy >= 20:
                    self.afcsy_step_size = 3
                elif 19.999 > sy >= 10:
                    self.afcsy_step_size = 2
                elif 9.999 > sx >= 5:
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
            self._spring_handle.setCondition(self.spring_x)
            self._spring_handle.setCondition(self.spring_y)

            # hands_off_deadzone = 0.02
            if telem_data.get("hpgHandsOnCyclic", 0):
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

            self._spring_handle.start()

    def _update_cyclic_trim(self, telem_data):
        # Trimming is handled by the AFCS integration - override parent class function
        pass

    def _update_pedals(self, telem_data):

        if telem_data.get("FFBType") != 'pedals':
            return

        if self.telemffb_controls_axes and not self.local_disable_axis_control:
            input_data = HapticEffect.device.get_input()
            phys_x, phys_y = input_data.axisXY()
            telem_data['phys_x'] = phys_x


            if telem_data.get('hpgFeetOnPedals', 0):
                feet_on_dict = self.check_feet_on(self.feet_off_deadzone)
            else:
                feet_on_dict = self.check_feet_on(self.feet_on_deadzone)
            feet_on_pedals = feet_on_dict['result']
            dev_x = feet_on_dict['deviation']

            if feet_on_pedals:
                self._simconnect.set_simdatum_to_msfs("L:FFB_FEET_ON_PEDALS", 1, units="number")
                self.feet_on_active = True

            else:
                self._simconnect.set_simdatum_to_msfs("L:FFB_FEET_ON_PEDALS", 0, units="number")
                self.feet_on_active = False

            x_scale = clamp(self.rudder_x_axis_scale, 0, 1)

            self._spring_handle.name = "pedal_ap_spring"

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

                self._spring_handle.setCondition(self.spring_x)
                # self.damper.damper(coef_x=int(4096 * self.pedal_dampening_gain)).start()
                self._spring_handle.start()
                logging.debug(f"self.cpO_x:{self.cpO_x}, phys_x:{phys_x}")
                if self.cpO_x / 4096 - 0.1 < phys_x < self.cpO_x / 4096 + 0.1:
                    # dont start sending position until physical pedals have centered
                    self.pedals_init = 1
                    logging.info("Pedals Initialized")
                else:
                    return


            sema_yaw = telem_data.get("hpgSEMAyaw", 0)

            # sema_x_avg = self.smoother.get_rolling_average('s_sema_x', sema_x, window_ms=500)
            # sema_y_avg = self.smoother.get_rolling_average('s_sema_y', sema_y, window_ms=500)

            sx = round(abs(sema_yaw), 3)
            if 100 >= sx >= 50:
                self.afcsx_step_size = 6
            elif 49.999 > sx >= 20:
                self.afcsx_step_size = 4
            elif 19.999 > sx >= 10:
                self.afcsx_step_size = 3
            elif 9.999 > sx >= 5:
                self.afcsx_step_size = 1
            elif 4.999 > sx >= 0:
                self.afcsx_step_size = 1
            else:
                self.afcsx_step_size = 0

            telem_data['_sx'] = sx
            telem_data['_afcsx_step_size'] = self.afcsx_step_size
            if not (self.feet_on_active):
                # print("doing it")
                if abs(sema_yaw) > self.sema_yaw_max:
                    # print(f"sema_x:{sema_x}")
                    if sema_yaw > self.sema_yaw_max:
                        self.cpO_x -= self.afcsx_step_size
                    elif sema_yaw < -self.sema_yaw_max:
                        self.cpO_x += self.afcsx_step_size
            telem_data['_cp0_x'] = self.cpO_x

            self.spring_x.cpOffset = int(self.cpO_x)
            self._spring_handle.setCondition(self.spring_x)
            self._spring_handle.start()

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
        if not self.telemffb_controls_axes and not self.local_disable_axis_control:
            self.flag_error("Aircraft is configured as class HPGHelicopter.  For proper integration, TelemFFB must send axis position to MSFS.\n\nPlease enable 'telemffb_controls_axes' in your config and unbind the collective axes in MSFS settings")
            return
        self._spring_handle.name = "collective_ap_spring"
        # self.damper = effects["collective_damper"].damper()

        if self.force_trim_enabled and self.force_trim_button:
            input_data = HapticEffect.device.get_input()
            force_trim_pressed = input_data.isButtonPressed(self.force_trim_button)
            if self._sim_is_msfs() and force_trim_pressed:
                self._simconnect.send_event_to_msfs("AUTO_THROTTLE_DISCONNECT", 1)

        collective_tr = telem_data.get("hpgCollectiveRelease", 0)
        afcs_mode = telem_data.get("hpgCollectiveAfcsMode", 0)
        collective_pos = telem_data.get("CollectivePos", 0)

        # input_data = HapticEffect.device.getInput()
        # phys_x, phys_y = input_data.axisXY()
        # SimVar("h145CollectiveRelease", "L:H145_SDK_AFCS_COLLECTIVE_TRIM_IS_RELEASED", "bool"),
        # SimVar("h145CollectiveAfcsMode", "L:H145_SDK_AFCS_MODE_COLLECTIVE", "number"),
        # SimVar("CollectivePos", "COLLECTIVE POSITION", "percent over 100"),
        input_data = HapticEffect.device.get_input()
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

            self._spring_handle.setCondition(self.spring_y)
            # self.damper.damper(coef_y=4096).start()
            self._spring_handle.start()
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

                # self.damper.damper(coef_y=0).start()
                self.spring_y.negativeCoefficient = self.spring_y.positiveCoefficient = int(4096 * self.trim_release_spring_gain)

                self._spring_handle.setCondition(self.spring_y)
                self._spring_handle.start()

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

                # self.damper.damper(coef_y=0).start()
                self.spring_y.negativeCoefficient = self.spring_y.positiveCoefficient = round(self.collective_spring_coeff_y)

                self._spring_handle.setCondition(self.spring_y)
                self._spring_handle.start()

        else:
            if collective_tr:

                self.cpO_y = round(4096*utils.clamp(phys_y, -1, 1))
                # print(self.cpO_y)
                self.spring_y.cpOffset = self.cpO_y

                # self.damper.damper(coef_y=0).start()
                self.spring_y.negativeCoefficient = self.spring_y.positiveCoefficient = int(4096 * self.trim_release_spring_gain)

                self._spring_handle.setCondition(self.spring_y)
                self._spring_handle.start()

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
                # self.damper.damper(coef_y=0).start()

                self._spring_handle.setCondition(self.spring_y)
                self._spring_handle.start()

    def _update_vrs_effect(self, telem_data):
        vrs_onset = telem_data.get("hpgVRSDatum", 0)
        vrs_certain = telem_data.get("hpgVRSIsInVRS", 0)


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




