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
from typing import override

from telemffb.sim.msfs_xp.MsfsXpNosewheelShimmyMixIn import MsfsXpNosewheelShimmyMixIn
from telemffb.sim.msfs_xp.MsfsXpTrimwheelMixIn import MsfsXpTrimwheelMixIn
from telemffb.sim.msfs_xp.MsfsXpFlightControlsMixIn import MsfsXpFlightControlsMixIn

import telemffb.utils as utils
from telemffb.SettingsManager import SpringModeEnum
from telemffb.hw.ffb_rhino import FFBReport_SetCondition, HapticEffect, EFFECT_SQUARE
from telemffb.sim.aircraft_base import AircraftBase
from telemffb.util.Vector import Vector
from telemffb.util.conversions import rad
from telemffb.sim.msfs_xp.TurbulenceMixIn import TurbulenceMixIn
# removed local 'overrides' helper in favor of typing.override


from telemffb.sim.base.DynamicSpringMixin import DynamicSpringMixin
from telemffb.sim.base.AdvancedSpringMixIn import AdvancedSpringMixIn
from telemffb.sim.base.AoAEffectsMixIn import AoAEffectsMixIn

import logging
from telemffb.sim.BaseTelemetryData import BaseTelemetryData


class Aircraft(
    AircraftBase,
    TurbulenceMixIn,
    MsfsXpTrimwheelMixIn,
    MsfsXpNosewheelShimmyMixIn,
    MsfsXpFlightControlsMixIn
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

    def __init__(self, name, **kwargs) -> None:
        super().__init__(name)

        # clear any existing effects
        self.effects.clear()
        # self.spring = HapticEffect().spring()

        self.cyclic_trim_release_active = 0
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

        self.spring_mode = SpringModeEnum.BASIC.name

        self.last_pedal_x = 0

    def msfs_update_stick_shaker(self, telem_data: BaseTelemetryData):
        if not self.is_joystick():
            return
        if not self._sim_is_msfs():
            return

        if not self.enable_stick_shaker:
            self.effects["stick_shaker"].destroy()
            return

        stall = telem_data.StallWarning or 0
        if stall:
            self.effects["stick_shaker"].periodic(14, self.stick_shaker_intensity, 0, EFFECT_SQUARE).start()
        else:
            self.effects["stick_shaker"].destroy()

    def msfs_override_collective_spring(self):
        """
        Method specifically intended to start a spring with force=0 for use in fixed wing aircraft so it may be stowed
        and kept out of the way
        .
        Option to leave spring active also exists
        """
        if not self.is_collective(): return
        if self.is_helicopter(): return # if helicopter, do nothing, we are only doing this for fixed wing aircraft with collective

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

    @override
    def on_event(self, event, *args):
        super().on_event(event, *args)
        logging.info(f"on_event {event} {args}")

        if event == "STOP":
            self.on_timeout()

    @override
    def on_telemetry(self, telem_data: BaseTelemetryData):
        self.effects["pause_spring"].destroy()

        if telem_data.Parked: # MSFS in Hangar
            return

        if self._sim_is_xplane():
            self.toggle_xp_control()

        if self._sim_is_xplane():
            incidence_vec = Vector(telem_data.VelAcf)
        else:
            incidence_vec = Vector(telem_data.VelWorld)
            wind_vec = Vector(telem_data.AmbWind)
            incidence_vec = incidence_vec - wind_vec
            incidence_vec = incidence_vec.rotY(-(telem_data.Heading * rad))
            incidence_vec = incidence_vec.rotX(-telem_data.Pitch * rad)
            incidence_vec = incidence_vec.rotZ(-telem_data.Roll * rad)

        telem_data.Incidence = list(incidence_vec)

        #
        ### Generic Aircraft Class Telemetry Handler
        if not "AircraftClass" in telem_data:
            telem_data.AircraftClass = "GenericAircraft"  # inject aircraft class into telemetry

        if self.is_trimwheel():
            # A trimwheel is a single-purpose device: run ONLY the trimwheel
            # routine. Skipping super() stops the entire cooperative effects
            # chain (engine rumble, buffeting, g-force, ...) — those effects
            # played through the wheel after the mixin refactor lost the old
            # early-return guards. Subclass on_telemetry overrides with code
            # AFTER their super() call must carry their own is_trimwheel()
            # guard (cf. Helicopter/GliderAircraft/FlyInsideHelicopter).
            self.msfs_update_trimwheel(telem_data)
            return

        super().on_telemetry(telem_data)

        self.msfs_update_stick_shaker(telem_data)

    @override
    def on_timeout(self):
        if not self.effects["pause_spring"].started:
            super().on_timeout()

        self.cyclic_spring_init = 0

        if self.center_spring_on_pause:
            self.spring_x.set_coefficient(1.0)
            self.spring_y.set_coefficient(1.0)
            self.spring_x.set_offset(0.0)
            self.spring_y.set_offset(0.0)

            pause_spring = self.effects["pause_spring"].spring()
            pause_spring.setCondition(self.spring_x)
            pause_spring.setCondition(self.spring_y)
            pause_spring.start()
