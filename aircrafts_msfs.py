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
from simconnect import *
from ctypes import byref, cast, sizeof
from time import sleep
from pprint import pprint
import sys
import logging
import utils
from typing import List, Dict
from utils import clamp, HighPassFilter, Derivative, Dispenser

from ffb_rhino import HapticEffect, FFBReport_SetCondition
from aircraft_base import AircraftBase

# by accessing effects dict directly new effects will be automatically allocated
# example: effects["myUniqueName"]
effects: Dict[str, HapticEffect] = utils.Dispenser(HapticEffect)

# Highpass filter dispenser
HPFs: Dict[str, utils.HighPassFilter] = utils.Dispenser(utils.HighPassFilter)

# Lowpass filter dispenser
LPFs: Dict[str, utils.LowPassFilter] = utils.Dispenser(utils.LowPassFilter)

hpf = Dispenser(HighPassFilter)

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

    engine_rumble: int = 0  # Engine Rumble - Disabled by default - set to 1 in config file to enable

    runway_rumble_intensity: float = 1.0  # peak runway intensity, 0 to disable

    gun_vibration_intensity: float = 0.12  # peak gunfire vibration intensity, 0 to disable
    cm_vibration_intensity: float = 0.12  # peak countermeasure release vibration intensity, 0 to disable
    weapon_release_intensity: float = 0.12  # peak weapon release vibration intensity, 0 to disable
    weapon_effect_direction: int = 45  # Affects the direction of force applied for gun/cm/weapon release effect, Set to -1 for random direction

    speedbrake_motion_intensity: float = 0.12  # peak vibration intensity when speed brake is moving, 0 to disable
    speedbrake_buffet_intensity: float = 0.15  # peak buffeting intensity when speed brake deployed,  0 to disable

    spoiler_motion_intensity: float = 0.0  # peak vibration intensity when spoilers is moving, 0 to disable
    spoiler_buffet_intensity: float = 0.15  # peak buffeting intensity when spoilers deployed,  0 to disable

    gear_motion_intensity: float = 0.12  # peak vibration intensity when gear is moving, 0 to disable
    gear_buffet_intensity: float = 0.15  # peak buffeting intensity when gear down during flight,  0 to disable

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

    aircraft_is_fbw = 0
    fbw_elevator_gain = 0.8
    fbw_aileron_gain = 0.8

    def __init__(self, name, **kwargs) -> None:
        super().__init__(name)
        # clear any existing effects
        for e in effects.values(): e.destroy()
        effects.clear()
        self.spring = HapticEffect().spring()
        self.spring_x = FFBReport_SetCondition(parameterBlockOffset=0)
        self.spring_y = FFBReport_SetCondition(parameterBlockOffset=1)
        self.spring.start()
        self.const_y = HapticEffect().constant(0, 0)

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

        self.aoa_gain = 1.0
        self.g_force_gain = 0.1
        self.prop_diameter = 1.5

        self.elevator_droop_moment = 0.1  # in FFB force units

        # how much air flow the elevator receives from the propeller
        self.elevator_prop_flow_ratio = 1.0

        # scale the dynamic pressure to ffb friendly values
        self.dyn_pressure_scale = 0.005
        self.max_aoa_cf_force: float = 0.2  # CF force sent to device at %stall_aoa

    def _update_fbw_flight_controls(self, telem_data):
        self.spring_y.positiveCoefficient = clamp(int(4096 * self.fbw_elevator_gain), 0, 4096)
        # logging.debug(f"Elev Coeef: {elevator_coeff}")
        self.spring_y.negativeCoefficient = self.spring_y.positiveCoefficient
        self.spring_y.cpOffset = 0  # -clamp(int(4096*elevator_offs), -4096, 4096)

        self.spring_x.positiveCoefficient = clamp(int(4096 * self.fbw_aileron_gain), 0, 4096)
        self.spring_x.negativeCoefficient = self.spring_x.positiveCoefficient

        # update spring data
        self.spring.effect.setCondition(self.spring_y)
        self.spring.effect.setCondition(self.spring_x)


    def _update_flight_controls(self, telem_data):
        # calculations loosely based on FLightGear FFB page:
        # https://wiki.flightgear.org/Force_feedback
        # https://github.com/viktorradnai/fg-haptic/blob/master/force-feedback.nas

        elev_base_gain = 0
        ailer_base_gain = 0
        if self.aircraft_is_fbw or telem_data.get("ACisFBW"):
            logging.debug ("FBW Setting enabled, running fbw_flight_controls")
            self._update_fbw_flight_controls(telem_data)
            return
        elif telem_data.get("AircraftClass") == "Helicopter":
            logging.debug("Aircraft is Helicopter, aborting update_flight_controls")
            return
        if self.aircraft_is_spring_centered:
            elev_base_gain = self.aileron_spring_gain
            ailer_base_gain = self.elevator_spring_gain
            logging.debug(f"Aircraft controls are center sprung, setting x:y base gain to{elev_base_gain}:{ailer_base_gain}")

        base_elev_coeff = round(clamp((elev_base_gain * 4096), 0, 4096))
        base_ailer_coeff = round(clamp((ailer_base_gain * 4096), 0, 4096))
        #logging.info(f"Base Elev/Ailer coeff = {base_elev_coeff}/{base_ailer_coeff}")

        rudder_angle = telem_data["RudderDefl"] * rad  # + trim?

        # print(data["ElevDefl"] / data["ElevDeflPct"] * 100)

        slip_angle = telem_data["SideSlip"] * rad
        g_force = telem_data["G"] # this includes earths gravity

        incidence_vec = utils.Vector(telem_data["VelWorld"])
        wind_vec = utils.Vector(telem_data["AmbWind"])
        incidence_vec = incidence_vec - wind_vec
        # Rotate the vector from world frame into body frame
        incidence_vec = incidence_vec.rotY(-(telem_data["Heading"] * rad))
        incidence_vec = incidence_vec.rotX(-telem_data["Pitch"] * rad)
        incidence_vec = incidence_vec.rotZ(-telem_data["Roll"] * rad)
        _airspeed = incidence_vec.z

        # calculate air flow velocity exiting the prop
        # based on https://www.grc.nasa.gov/www/k-12/airplane/propth.html
        _prop_air_vel = sqrt(
            2 * telem_data["PropThrust1"] / (
                        telem_data["AirDensity"] * (math.pi * (self.prop_diameter / 2) ** 2)) + _airspeed ** 2)

        if abs(incidence_vec.y) > 0.5 and _prop_air_vel > 1: # avoid edge cases
            _elevator_aoa = atan2(-incidence_vec.y, _prop_air_vel) * deg
        else:
            _elevator_aoa = 0

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
        _aoa_term = sin(( _elevator_aoa - telem_data["ElevDefl"]) * rad) * self.aoa_gain * _elev_dyn_pressure * _slip_gain
        telem_data["_aoa_term"] = _aoa_term

        _G_term = (self.g_force_gain * g_force)
        telem_data["_G_term"] = _G_term

        #       hpf_pitch_acc = hpf.get("xacc", 3).update(data["RelWndY"]) # test stuff
        #       data["_hpf_pitch_acc"] = hpf_pitch_acc # test stuff

        self.spring_y.positiveCoefficient = clamp(int(4096 * elevator_coeff), base_elev_coeff, 4096)
        ec = clamp(int(4096 * elevator_coeff), base_elev_coeff, 4096)
        logging.debug(f"Elev Coeef: {ec}")
        self.spring_y.negativeCoefficient = self.spring_y.positiveCoefficient
        self.spring_y.cpOffset = 0  # -clamp(int(4096*elevator_offs), -4096, 4096)
        ac = clamp(int(4096 * aileron_coeff), base_elev_coeff, 4096)
        logging.debug(f"Ailer Coeef: {ac}")

        self.spring_x.positiveCoefficient = clamp(int(4096 * aileron_coeff), base_ailer_coeff, 4096)
        self.spring_x.negativeCoefficient = self.spring_x.positiveCoefficient

        # update spring data
        self.spring.effect.setCondition(self.spring_y)
        self.spring.effect.setCondition(self.spring_x)

        # update constant forces
        cf_pitch = -_elevator_droop_term + _aoa_term - _G_term
        cf_pitch = clamp(cf_pitch, -1.0, 1.0)

        cf_roll = 0

        cf = utils.Vector2D(cf_pitch, cf_roll)
        mag, theta = cf.to_polar()
        self.const_y.constant(mag, theta*deg).start()

        rudder_angle = rudder_angle - slip_angle

    def on_telemetry(self, telem_data):
        if telem_data["Parked"]: # Aircraft is parked, do nothing
            return

        ### Generic Aircraft Class Telemetry Handler
        if not "AircraftClass" in telem_data:
            telem_data["AircraftClass"] = "GenericAircraft"  # inject aircraft class into telemetry
        self._update_runway_rumble(telem_data)
        self._update_buffeting(telem_data)
        self._update_flight_controls(telem_data)
        if self.flaps_motion_intensity > 0:
            flps = max(telem_data.get("Flaps", 0))
            self._update_flaps(flps)
        if self.gear_motion_intensity > 0:
            gear = max(telem_data.get("Gear", 0))
            self._update_landing_gear(gear, telem_data.get("TAS"), spd_thresh_low=130 * kt2ms, spd_thresh_high=200 * kt2ms)
        if self.deceleration_effect_enable and self.deceleration_effect_enable_areyoureallysure:
            if telem_data.get("AircraftClass") != "Helicopter":
                self._decel_effect(telem_data)
        if self.aoa_reduction_effect_enabled:
            self._aoa_reduction_force_effect(telem_data)

    def on_timeout(self):
        super().on_timeout()
        logging.debug("Timeout, preparing to stop effects")
        effects.clear()
        for e in effects.values():
            logging.debug(f"Timeout effect: {e}")
            e.stop()


class PropellerAircraft(Aircraft):
    """Generic Class for Prop aircraft"""
    engine_rumble: int = 0  # Engine Rumble - Disabled by default - set to 1 in config file to enable

    engine_rumble_intensity: float = 0.02
    engine_rumble_lowrpm = 450
    engine_rumble_lowrpm_intensity: float = 0.12
    engine_rumble_highrpm = 2800
    engine_rumble_highrpm_intensity: float = 0.06
    engine_max_rpm = 2700  # Assume engine RPM of 2700 at 'EngRPM' = 1.00 for aircraft not exporting 'ActualRPM' in lua script
    max_aoa_cf_force: float = 0.2  # CF force sent to device at %stall_aoa
    rpm_scale: float = 45

    _engine_rumble_is_playing = 0

    # run on every telemetry frame
    def on_telemetry(self, telem_data):
        ### Propeller Aircraft Class Telemetry Handler
        if telem_data.get("N") == None:
            return
        telem_data["AircraftClass"] = "PropellerAircraft"  # inject aircraft class into telemetry

        super().on_telemetry(telem_data)
        ## Engine Rumble
        current_rpm = telem_data["PropRPM"]
        if isinstance(current_rpm, list):
            current_rpm = max(current_rpm)
        if self.engine_rumble:
            self._update_engine_rumble(current_rpm)
        #self._update_aoa_effect(telem_data) # currently calculated in  _update_flight_controls
        if self.spoiler_motion_intensity > 0 or self.spoiler_buffet_intensity > 0:
            sp = max(telem_data.get("Spoilers", 0))
            self._update_spoiler(sp, telem_data.get("TAS"), spd_thresh_low=800*kt2ms, spd_thresh_hi=140*kt2ms )
        if self.gforce_effect_enable and self.gforce_effect_enable_areyoureallysure:
            super()._gforce_effect(telem_data)


class JetAircraft(Aircraft):
    """Generic Class for Jets"""
    # flaps_motion_intensity = 0.0

    _ab_is_playing = 0
    _jet_rumble_is_playing = 0

    # run on every telemetry frame
    def on_telemetry(self, telem_data):
        ## Jet Aircraft Telemetry Manager
        if telem_data.get("N") == None:
            return
        telem_data["AircraftClass"] = "JetAircraft"  # inject aircraft class into telemetry
        super().on_telemetry(telem_data)
        if self.spoiler_motion_intensity > 0 or self.spoiler_buffet_intensity > 0:
            sp = max(telem_data.get("Spoilers", 0))
            self._update_spoiler(sp, telem_data.get("TAS"), spd_thresh_low=150*kt2ms, spd_thresh_hi=300*kt2ms )
        if self.gforce_effect_enable and self.gforce_effect_enable_areyoureallysure:
            super()._gforce_effect(telem_data)
        if self.afterburner_effect_intensity > 0:
            self._update_ab_effect(telem_data)

class TurbopropAircraft(Aircraft):

    def on_telemetry(self, telem_data):
        if telem_data.get("N") == None:
            return
        telem_data["AircraftClass"] = "TurbopropAircraft"  # inject aircraft class into telemetry
        super().on_telemetry(telem_data)
        if self.spoiler_motion_intensity > 0 or self.spoiler_buffet_intensity > 0:
            sp = max(telem_data.get("Spoilers", 0))
            self._update_spoiler(sp, telem_data.get("TAS"), spd_thresh_low=120*kt2ms, spd_thresh_hi=260*kt2ms )
        if self.gforce_effect_enable and self.gforce_effect_enable_areyoureallysure:
            super()._gforce_effect(telem_data)

class GliderAircraft(Aircraft):

    def on_telemetry(self, telem_data):
        if telem_data.get("N") == None:
            return
        telem_data["AircraftClass"] = "GliderAircraft"  # inject aircraft class into telemetry
        super().on_telemetry(telem_data)
        if self.spoiler_motion_intensity > 0 or self.spoiler_buffet_intensity > 0:
            sp = max(telem_data.get("Spoilers", 0))
            self._update_spoiler(sp, telem_data.get("TAS"), spd_thresh_low=60*kt2ms, spd_thresh_hi=120*kt2ms )
        if self.gforce_effect_enable and self.gforce_effect_enable_areyoureallysure:
            super()._gforce_effect(telem_data)

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

    def on_telemetry(self, telem_data):
        self.speedbrake_motion_intensity = 0.0
        if telem_data.get("N") == None:
            return
        telem_data["AircraftClass"] = "Helicopter"  # inject aircraft class into telemetry
        super().on_telemetry(telem_data)
        self._calc_etl_effect(telem_data, blade_ct=self.rotor_blade_count)
        self._update_heli_engine_rumble(telem_data, blade_ct=self.rotor_blade_count)