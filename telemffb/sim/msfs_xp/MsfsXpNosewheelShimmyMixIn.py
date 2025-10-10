import telemffb.utils as utils
from telemffb.sim.base.AircraftEffectUtilsBase import AircraftEffectUtilsBase


import logging


class MsfsXpNosewheelShimmyMixIn(AircraftEffectUtilsBase):

    # user parameters
    nosewheel_shimmy: bool = False
    nosewheel_shimmy_intensity = 0.15
    nosewheel_shimmy_min_speed = 7
    nosewheel_shimmy_min_brakes = 0.6
    #end of user parameters

    def msfs_update_nosewheel_shimmy(self, telem_data):
        if not self._sim_is_msfs(): return
        if not self.is_pedals(): return
        if not self.nosewheel_shimmy: return
        if telem_data.get("IsTaildragger", 0):
            return

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

    def on_telemetry(self, telem_data: dict):
        super().on_telemetry(telem_data)
        self.msfs_update_nosewheel_shimmy(telem_data)