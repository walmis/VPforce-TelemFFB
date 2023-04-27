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
from simconnect import *
from ctypes import byref, cast, sizeof
from time import sleep
from pprint import pprint
import sys
import utils
from utils import clamp, HighPassFilter, Derivative, Dispenser

from ffb_rhino import HapticEffect, FFBReport_SetCondition

hpf = Dispenser(HighPassFilter)

deg = 180/math.pi
slugft3 = 0.00194032 # SI to slugft3
rad = 0.0174532925
ft = 3.28084 # m to ft
kt = 1.94384 # ms to kt

class Aircraft:
    def __init__(self, name, **kwargs) -> None:
        self.spring = HapticEffect().spring()
        self.spring_x = FFBReport_SetCondition(parameterBlockOffset=0)
        self.spring_y = FFBReport_SetCondition(parameterBlockOffset=1)
        self.spring.start()
        self.const_y = HapticEffect().constant(0,0)

        #aileron_max_deflection = 20.0*0.01745329
        self.elevator_max_deflection = 12.0*0.01745329
        #rudder_max_deflection = 20.0*0.01745329
        self.aileron_gain = 0.1
        self.elevator_gain = 0.1
        self.rudder_gain = 0.1
        self.slip_gain = 1.0
        self.stall_AoA = 18.0*0.01745329
        self.pusher_start_AoA = 900.0*0.01745329
        self.pusher_working_angle = 900.0*0.01745329
        self.wing_shadow_AoA = 900.0*0.01745329
        self.wing_shadow_angle = 900.0*0.01745329
        self.stick_shaker_AoA = 16.0*0.01745329

        self.elevator_area = 1
        self.aileron_area = 1
        self.aoa_gain = 1.0
        self.g_force_gain = 0.1
        self.prop_diameter = 1.5

        self.elevator_droop_moment = 0.1 # in FFB force units

        # how much air flow the elevator receives from the propeller
        self.elevator_prop_flow_ratio = 1.0

        #scale the dynamic pressure to ffb friendly values
        self.dyn_pressure_scale = 0.005
    
        print(kwargs)
        self.__dict__.update(kwargs)

    def on_timeout(self):
        pass

    def on_telemetry(self, data):
		# calculations loosely based on FLightGear FFB page: 
		# https://wiki.flightgear.org/Force_feedback
		# https://github.com/viktorradnai/fg-haptic/blob/master/force-feedback.nas

        rudder_angle = data["RudderDefl"]*rad # + trim?

        #print(data["ElevDefl"] / data["ElevDeflPct"] * 100)

        slip_angle = data["SideSlip"]*rad
        g_force = data["G"]

        #calculate air flow velocity exiting the prop
        #based on https://www.grc.nasa.gov/www/k-12/airplane/propth.html
        _prop_air_vel = math.sqrt(2*data["PropThrust1"] / (data["AirDensity"] * (math.pi*(self.prop_diameter/2)**2)) + data["TrueAirspeed"]**2 )

        if abs(data["RelWndY"]) > 0.5 and _prop_air_vel > 1:
            _elevator_aoa = math.atan2(-data["RelWndY"], _prop_air_vel)*deg
        else:
            _elevator_aoa = 0
            

        # calculate dynamic pressure based on air flow from propeller
        # elevator_prop_flow_ratio defines how much prop wash the elevator receives
        _elev_dyn_pressure = utils.mix(data["DynPressure"], 0.5 * data["AirDensity"] * _prop_air_vel**2, self.elevator_prop_flow_ratio) * self.dyn_pressure_scale
        
        #scale dynamic pressure to FFB friendly values
        _dyn_pressure = data["DynPressure"] * self.dyn_pressure_scale

        slip_gain = 1.0 - self.slip_gain * math.sin(slip_angle)
        data["_slip_gain"] = slip_gain

		# increasing G force causes increase in elevator droop effect
        _elevator_droop_term = self.elevator_droop_moment * g_force / (1 + _elev_dyn_pressure)
        data["_elevator_droop_term"] = _elevator_droop_term

        
        aileron_coeff = _dyn_pressure * self.aileron_gain * slip_gain * self.aileron_area

		# add data to telemetry packet so they become visible in the GUI output
        data["_prop_air_vel"] = _prop_air_vel 
        data["_elev_dyn_pressure"] = _elev_dyn_pressure

        elevator_coeff = (_elev_dyn_pressure) * self.elevator_gain * slip_gain * self.elevator_area
        data["_elev_coeff"] = elevator_coeff
        data["_aile_coeff"] = aileron_coeff

		_aoa_term =  math.sin(_elevator_aoa*rad) * self.aoa_gain
        data["_aoa_term"] = _aoa_term
        #data["_G_term"] = (self.g_force_gain * g_force)

        hpf_pitch_acc = hpf.get("xacc", 3).update(data["RelWndY"]) # test stuff
        data["_hpf_pitch_acc"] = hpf_pitch_acc # test stuff

        self.spring_y.positiveCoefficient = clamp(int(4096*elevator_coeff), 0, 4096)
        self.spring_y.negativeCoefficient = self.spring_y.positiveCoefficient
        self.spring_y.cpOffset = 0 #-clamp(int(4096*elevator_offs), -4096, 4096)

        self.spring_x.positiveCoefficient = clamp(int(4096*aileron_coeff), 0, 4096)
        self.spring_x.negativeCoefficient = self.spring_x.positiveCoefficient

		# update spring data
        self.spring.effect.setCondition(self.spring_y)
        self.spring.effect.setCondition(self.spring_x)

		# update constant forces
        #self.const_y.constant( clamp(self.hpf_pitch(-data["PitchRate"])*0.1, -0.1, 0.1), 0).start()
        self.const_y.constant( clamp(- _elevator_droop_term + data["_aoa_term"] , -1, 1), 0).start()

        rudder_angle = rudder_angle - slip_angle