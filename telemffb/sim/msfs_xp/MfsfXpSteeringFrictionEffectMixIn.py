import telemffb.utils as utils
from telemffb.sim.base.FFBForcesMixIn import FFBForcesMixIn
from telemffb.utils import clamp
from telemffb.sim.BaseTelemetryData import BaseTelemetryData


class MfsfXpSteeringFrictionEffectMixIn(FFBForcesMixIn):
    """Mixin for MSFS steering friction effect."""

    # user parameters
    steering_friction = 0
    steering_friction_intensity = 0.8
    steering_friction_spring = 0.5
    steering_friction_expo = -0.4
    # end of user parameters

    def __init__(self):
        super().__init__()
        self.friction_effect_overridden = False

    def msfs_update_steering_friction_effect(self, telem_data: BaseTelemetryData):
        if not self._sim_is_msfs(): return
        if not self.is_pedals(): return

        if not self.steering_friction:
            if self.friction_effect_overridden and self.effects['friction'].name == 'steering_friction':
                # If effect is disabled but was previously active, clean up and pass override control back to base friction effect
                utils.dbprint("purple", self.friction_effect_overridden, instance='pedals')
                self.friction_effect_overridden = False
                self.effects['friction'].name = 'none'
                self.effects["friction"].destroy()
                return
            return  # Hit this return if effect is simply disabled

        if not self.enable_friction_ovd:
            self.flag_error(
                "Steering Friction effect enabled but friction override not enabled")
            return

        on_ground = telem_data.SimOnGround or 0
        wos = (telem_data.WeightOnWheels or [0])[0]  # center steering wheel only
        gs = telem_data.GroundSpeed or 0
        #csa = telem_data.get("CenterSteerAngle", 0)
        wr = telem_data.WaterRudderExt or 0 # percent of rudder extension
        surface = telem_data.SurfaceType or 0

        if on_ground and (wos or surface == "Water"):
            # scale gs 0-40kt to 1-0
            scalespeed = utils.scale(gs, (0, 20), (1, 0))
            efriction = utils.expocurve(scalespeed * self.steering_friction_intensity, self.steering_friction_expo)
            # logging.info(f"wos {wos}  gs {gs}  efriction {efriction}")

            base_friction_coeff = int(self.friction_force * 4096)  # calculate coefficient of base effect setting as baseline
            usable_friction_range = 4096 - base_friction_coeff  # usable coefficient for this effect
            friction_coeff_add = int(usable_friction_range * efriction)  # ammount to add based on efriction calculation

            friction_force = clamp((base_friction_coeff + friction_coeff_add), 0, 4096)

            # self.friction_coeff = utils.clamp(int(efriction * 4096), int(self.friction_force * 4096), 4096)

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

    def on_telemetry(self, telem_data: BaseTelemetryData):
        super().on_telemetry(telem_data)
        self.msfs_update_steering_friction_effect(telem_data)