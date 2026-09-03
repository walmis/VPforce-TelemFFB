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
"""MSFS/X-Plane ground steering friction.

All of the on-ground steering-gear force logic for MSFS/X-Plane pedals is
self-contained in this mixin: parameter plumbing plus two alternative
strategies, gated by the internal ``steering_method`` flag.

The mixin sits in front of :class:`FFBForcesMixIn` in the class hierarchy
because the *friction* strategy overrides the base FFB friction effect
(its ``friction_force`` / ``enable_friction_ovd`` user settings and the
``friction_effect_overridden`` flag all come from FFBForcesMixIn).

Unit convention: all intermediate math stays in the normalized -1..1
range; device-unit (x4096 fixed point) values are normalized on the way in
and the single device-unit conversion happens at the end of the pipeline
in the ``FFBReport_SetCondition`` setters (float args are scaled by 4096
internally).
"""
from typing import override

import telemffb.utils as utils
from telemffb.sim.base.FFBForcesMixIn import FFBForcesMixIn
from telemffb.sim.BaseTelemetryData import BaseTelemetryData


class MsfsXpSteeringFrictionMixIn(FFBForcesMixIn):
    """Ground steering friction for MSFS/X-Plane pedals.

    Two alternative implementations, selected by the internal (non-XML)
    ``steering_method`` flag:

    - ``'spring'`` (default): adds a steering-gear spring vector on top of
      the rudder spring condition (``_apply_steering_friction``).
    - ``'friction'``: overrides the base FFB friction effect with a
      speed-scaled ground friction value (``msfs_update_steering_friction_effect``).

    The spring method is the live/current implementation; the friction method
    is the older implementation kept behind the internal flag.
    """

    # user parameters
    # (defaults match the shipping defaults.xml entries; see docs/defaults_xml_reference.md)
    steering_friction = 0  # bool: enable the effect (Experimental)
    steering_friction_intensity = 0.8  # 0..1: max ground friction fraction
    steering_friction_spring = 0.5  # 0..100: steering-gear spring strength
    steering_friction_expo = -0.4  # -9..0: speed-vs-friction curve
    # end of user parameters

    # internal parameters (no defaults.xml entries; chosen in code only)
    steering_method = 'spring'  # 'spring' or 'friction'

    @override
    def on_telemetry(self, telem_data: BaseTelemetryData):
        super().on_telemetry(telem_data)
        self.msfs_update_steering_friction_effect(telem_data)

    @override
    def on_timeout(self):
        super().on_timeout()

    def _update_pedals_spring(self, telem_data: BaseTelemetryData, phys_rudder_x_offs: float, rudder_coeff: float):
        """Update the pedals rudder spring state for the active steering method.

        Both inputs are normalized (-1..1); the device-unit conversion happens
        in the FFBReport_SetCondition setters (float args are scaled by 4096
        internally).

        spring method (default): steering-gear spring vector written onto the
        pedals spring condition (``self.spring_x``).
        friction method: override of the FFB friction effect (the pedals
        spring keeps the rudder coefficient already set by the caller).
        """
        if self.steering_method == 'friction':
            self.msfs_update_steering_friction_effect(telem_data)
            return
        self._apply_steering_friction(telem_data, phys_rudder_x_offs, rudder_coeff)

    def _apply_steering_friction(self, telem_data: BaseTelemetryData, phys_rudder_x_offs: float, rudder_coeff: float):
        """Apply the spring-based steering gear effect when on ground.

        All intermediate math stays normalized (-1..1); the single
        device-unit conversion happens at the setters (float args are
        scaled by 4096 internally).

        Telemetry:
            Read:    SimOnGround          - int (0 or 1); effect only applies on the ground
                      WeightOnWheels[0]   - float (0.0-1.0); center wheel must be loaded
                      SurfaceType         - str; "Water" path also allows weight-on-wheels == 0
                      CenterSteerAnglePct - float (-1..1); steering wheel deflection pct
                      WaterRudderExt      - float (0.0-1.0); water rudder extension ratio
        """
        if not self.steering_friction:
            return

        on_ground = telem_data.SimOnGround or 0
        wos = (telem_data.WeightOnWheels or [0])[0]
        surface = telem_data.SurfaceType or 0

        if not (on_ground and (wos or surface == "Water")):
            return

        rudder_angle = 30  # assumed rudder travel (degrees)
        dynamic_angle = phys_rudder_x_offs * rudder_angle
        dynamic_force = rudder_coeff  # normalized -1..1 (float; scaled by setters)
        csa = telem_data.CenterSteerAnglePct or 0
        steer_angle = csa * rudder_angle
        # steering_friction_spring is a 0..100 XML value; /40 scales it onto the ~0..1
        # magnitude used for the steering-gear spring vector (kept as-is: tuned by feel)
        steer_force = (self.steering_friction_spring / 40)

        wr = telem_data.WaterRudderExt or 0
        if surface == "Water":
            steer_force *= wr

        result_angle_percent, result_mag = utils.add_vectors_deg(
            dynamic_angle, dynamic_force, steer_angle, steer_force
        )

        self.spring_x.set_coefficient(utils.clamp(result_mag, -1, 1), True)
        self.spring_x.set_offset(result_angle_percent / rudder_angle)

    def msfs_update_steering_friction_effect(self, telem_data: BaseTelemetryData):
        """Override the FFB friction effect with speed-dependent ground steering friction.

        All intermediate math stays normalized (0..1); the single device-unit
        conversion happens in the friction effect setter (float args are
        scaled by 4096 internally).

        Telemetry:
            Read:    SimOnGround       - int (0 or 1); effect active only when on the ground (1)
                      WeightOnWheels[0] - float (compression 0.0-1.0); center/nose wheel only;
                                          must be non-zero (or SurfaceType="Water") to apply
                      GroundSpeed       - float (m/s); scales friction via expo curve
                      WaterRudderExt    - float (0.0-1.0); water rudder extension ratio
                      SurfaceType       - str; "Water" enables the water-rudder friction path
            Written: _pct_steer_f      - float (0.0-1.0; fraction of usable friction range applied)
        """
        if not self._sim_is_msfs():
            return
        if not self.is_pedals():
            return

        if not self.steering_friction:
            if self.friction_effect_overridden and self.effects['friction'].name == 'steering_friction':
                # effect just disabled: hand override control back to the base
                # FFB-forces friction effect and tear down our override
                utils.dbprint("purple", self.friction_effect_overridden, instance='pedals')
                self.friction_effect_overridden = False
                self.effects['friction'].name = 'none'
                self.effects["friction"].destroy()
            return

        if not self.enable_friction_ovd:
            self.flag_error("Steering Friction effect enabled but friction override not enabled")
            return

        on_ground = telem_data.SimOnGround or 0
        wos = (telem_data.WeightOnWheels or [0])[0]  # center steering wheel only
        gs = telem_data.GroundSpeed or 0
        wr = telem_data.WaterRudderExt or 0  # percent of rudder extension
        surface = telem_data.SurfaceType or 0

        if on_ground and (wos or surface == "Water"):
            scalespeed = utils.scale(gs, (0, 20), (1, 0))  # scale gs 0-20 m/s to 1-0
            efriction = utils.expocurve(scalespeed * self.steering_friction_intensity, self.steering_friction_expo)

            # All intermediate math stays normalized (0..1); the single
            # device-unit conversion happens in the friction effect setter
            # (float args are scaled by 4096 internally).
            base_friction_coeff = utils.clamp(self.friction_force, 0.0, 1.0)  # baseline from the base effect setting
            usable_friction_range = 1.0 - base_friction_coeff                 # coefficient headroom for this effect
            friction_coeff_add = usable_friction_range * efriction           # amount to add based on efriction calculation

            friction_force = utils.clamp(base_friction_coeff + friction_coeff_add, 0.0, 1.0)

            if surface == "Water":
                friction_force *= wr

            if not usable_friction_range:
                return

            telem_data._pct_steer_f = friction_coeff_add / usable_friction_range
            self._ipc_telem["_pct_steer_f"] = friction_coeff_add / usable_friction_range

            self.friction_effect_overridden = True

            self.effects["friction"].name = "steering_friction"
            self.effects["friction"].friction(friction_force, friction_force).start()
        else:
            # clean up and pass control back to base effect when wheel no longer on ground
            if self.friction_effect_overridden and self.effects['friction'].name == 'steering_friction':
                self.friction_effect_overridden = False
                self.effects["friction"].destroy()
