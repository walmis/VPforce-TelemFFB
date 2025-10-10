#
# This file is part of the TelemFFB distribution (https://github.com/walmis/TelemFFB).
# Copyright (c) 2023 Valmantas Palikša.
# Copyright (c) 2023 Micah Frisby
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

from telemffb.sim.msfs_xp.MfsfXpSteeringFrictionEffectMixIn import MfsfXpSteeringFrictionEffectMixIn
from telemffb.sim.msfs_xp.MsfsXpTrimwheelMixIn import MsfsXpTrimwheelMixIn
import telemffb.utils as utils
from telemffb.SettingsManager import SpringModeEnum
from telemffb.hw.ffb_rhino import FFBReport_SetCondition, HapticEffect, EFFECT_SQUARE
from telemffb.sim.aircraft_base import AircraftBase
from telemffb.util.Vector import Vector
from telemffb.util.conversions import rad
from telemffb.util.TurbulenceModulator import TurbulenceModulator
from telemffb.utils import overrides


from telemffb.sim.base.DynamicSpringMixin import DynamicSpringMixin
from telemffb.sim.base.AdvancedSpringMixIn import AdvancedSpringMixIn
from telemffb.sim.base.AoAEffectsMixIn import AoAEffectsMixIn
from telemffb.sim.base.AircraftEffectUtilsBase import AircraftEffectUtilsBase

import logging

class Aircraft(
    MsfsXpTrimwheelMixIn, 
    MfsfXpSteeringFrictionEffectMixIn
):
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

    aircraft_is_spring_centered = 0   #deprecated
    spring_centered_elev_gain = 0.5
    spring_centered_ailer_gain = 0.5




    force_trim_enabled = 0
    include_dynamic_stick_forces = True

    elevator_force_trim = 0
    aileron_force_trim = 0

    smoother = utils.Smoother()
    dampener = utils.Derivative()
    center_spring_on_pause = False

    use_legacy_bindings = False





    ######## PEDAL SPECIFIC

    nosewheel_shimmy = 0
    nosewheel_shimmy_intensity = 0.15
    nosewheel_shimmy_min_speed = 7
    nosewheel_shimmy_min_brakes = 0.6



    ######## TRIMWHEEL SPECIFIC


    trim_active = False

    turbulence_effect_enable: bool = False
    turbulence_hpf_alpha: float = 0.0
    turbulence_smoothing_alpha: float = 0.0
    turbulence_sensitivity: float = 0.0
    turbulence_intensity: float = 0.0


    # trimwheel settings




    def __init__(self, name, **kwargs) -> None:
        super().__init__(name)
        self.turbulence_modulator = TurbulenceModulator()

        # clear any existing effects
        for e in self.effects.values(): e.destroy()
        self.effects.clear()
        # self.spring = HapticEffect().spring()


        self.const_force = HapticEffect().constant(0, 0)

        # aileron_max_deflection = 20.0*0.01745329
        self.elevator_max_deflection = 12.0 * 0.01745329
        # rudder_max_deflection = 20.0*0.01745329

        self.stall_AoA = 18.0 * 0.01745329
        self.pusher_start_AoA = 900.0 * 0.01745329
        self.pusher_working_angle = 900.0 * 0.01745329
        self.wing_shadow_AoA = 900.0 * 0.01745329
        self.wing_shadow_angle = 900.0 * 0.01745329
        self.stick_shaker_AoA = 16.0 * 0.01745329

        # FFB force value per lateral G

        self.aoa_gain = 0.3




        # scale the dynamic pressure to ffb friendly values
        self.dyn_pressure_scale = 0.005
        self.max_aoa_cf_force: float = 0.2  # CF force sent to device at %stall_aoa

        self.cyclic_trim_release_active = 0
        self.cyclic_spring_init = 0
        self.cyclic_center = [0.0, 0.0]  # x, y
        self.collective_spring_init = 0
        self.force_disable_collective_gain = True
        self.trim_release_spring_gain = 0

        self.force_trim_release_active = 0
        self.force_trim_spring_init = 0
        self.stick_center = [0, 0]  # x, y

        self.force_trim_x_offset = 0
        self.force_trim_y_offset = 0



        self.enable_stick_shaker = 0
        self.stick_shaker_intensity = 0





        self.trimwheel_init = False

        self.spring_mode = SpringModeEnum.BASIC.name

        self.last_pedal_x = 0
        self.last_trimwheel_y = None


    def msfs_update_nosewheel_shimmy(self, telem_data):
        curve = 2.5
        # freq = 8
        freq_lo = 8
        freq_hi = 16
        brakes = telem_data.get("Brakes", (0, 0))
        on_ground = telem_data.get("SimOnGround", 0)
        wow = sum(telem_data.get("WeightOnWheels", 0))
        if not wow or not on_ground:
            self.effects.dispose("nw_shimmy")
            return
        gs = telem_data.get("GroundSpeed", 0)

        freq = int(utils.scale(gs, (self.nosewheel_shimmy_min_speed, self.nosewheel_shimmy_min_speed*3), (freq_lo, freq_hi)))
        logging.debug(f"brakes = {brakes}")
        avg_brakes = sum(brakes) / len(brakes)
        if avg_brakes >= self.nosewheel_shimmy_min_brakes and gs > self.nosewheel_shimmy_min_speed:
            shimmy = utils.non_linear_scaling(avg_brakes, self.nosewheel_shimmy_min_brakes, 1.0, curvature=curve) * self.nosewheel_shimmy_intensity
            logging.debug(f"Nosewheel Shimmy intensity calculation: (BrakesPct:{avg_brakes} | GS:{gs} | RT Intensity: {shimmy}")
            self.effects["nw_shimmy"].periodic(freq, shimmy, 90).start()
        else:
            self.effects.dispose("nw_shimmy")




    def update_turbulence(self):
        if self.turbulence_effect_enable:
            force, dir = self.turbulence_modulator.update(self.telem_data, self.turbulence_hpf_alpha, self.turbulence_smoothing_alpha, self.turbulence_sensitivity, self.turbulence_intensity)
            force = round(force, 4)
            self.effects['turbulence'].constant(force, dir).start()

            #print(f"force:{force} dir:{dir}")
        else:
            self.effects['turbulence'].destroy()







    def msfs_update_stick_shaker(self, telem_data):
        if not self._sim_is_msfs():
            return

        if not self.enable_stick_shaker:
            self.effects['stick_shaker'].destroy()
            return

        stall = telem_data.get('StallWarning', 0)
        if stall:
            self.effects['stick_shaker'].periodic(14, self.stick_shaker_intensity, 0, EFFECT_SQUARE).start()
        else:
            self.effects['stick_shaker'].destroy()






    def msfs_override_collective_spring(self):
        """
        Method specifically intended to start a spring with force=0 for use in fixed wing aircraft so it may be stowed
        and kept out of the way
        .
        Option to leave spring active also exists
        """
        if not self.is_collective(): return
        if not self.is_helicopter(): return

        self.spring = self.effects["collective_ap_spring"].spring()

        if not self.force_disable_collective_gain:
            self.spring_y.set_coefficient(0.0)
            self.spring_y.set_offset(0.0)
            self.spring.setCondition(self.spring_y)
            self.spring.start(override=True)
        else:
            self.spring_y.set_coefficient(0.0)
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
        self.effects["pause_spring"].destroy()

        if telem_data.get('Parked', 0): # MSFS in Hangar
            return

        if self._sim_is_xplane():
            self.toggle_xp_control()

        if self._sim_is_xplane():
            incidence_vec = Vector(telem_data["VelAcf"])
        else:
            incidence_vec = Vector(telem_data["VelWorld"])
            wind_vec = Vector(telem_data["AmbWind"])
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

        super().on_telemetry(telem_data)

        if self.is_joystick():
            self.update_turbulence()

        if self.is_trimwheel():
            self.msfs_update_trimwheel(telem_data)
            return

        self.msfs_update_stick_shaker(telem_data)
        self.msfs_update_flight_controls(telem_data)

        if self._sim_is_msfs() and self.is_pedals():
            if self.nosewheel_shimmy and not telem_data.get("IsTaildragger", 0):
                self.msfs_update_nosewheel_shimmy(telem_data)
            self.msfs_update_steering_friction_effect(telem_data)

    @overrides(AircraftBase)
    def on_timeout(self):
        if not self.effects["pause_spring"].started:
            super().on_timeout()

        self.cyclic_spring_init = 0
        self.trimwheel_init = 0


        self.const_force.stop()
        self._spring_handle.stop()
        if self.center_spring_on_pause:
            self.spring_x.set_coefficient(1.0)
            self.spring_y.set_coefficient(1.0)
            self.spring_x.set_offset(0.0)
            self.spring_y.set_offset(0.0)

            pause_spring = self.effects["pause_spring"].spring()
            pause_spring.setCondition(self.spring_x)
            pause_spring.setCondition(self.spring_y)
            pause_spring.start()