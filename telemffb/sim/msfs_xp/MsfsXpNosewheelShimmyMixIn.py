import telemffb.utils as utils
from telemffb.sim.base.AircraftEffectUtilsBase import AircraftEffectUtilsBase


import logging
from telemffb.sim.BaseTelemetryData import BaseTelemetryData


class MsfsXpNosewheelShimmyMixIn(AircraftEffectUtilsBase):

    # user parameters
    nosewheel_shimmy: bool = False
    nosewheel_shimmy_intensity = 0.15
    nosewheel_shimmy_min_speed = 7
    nosewheel_shimmy_min_brakes = 0.6
    #end of user parameters

    def msfs_update_nosewheel_shimmy(self, telem_data: BaseTelemetryData):
        """Apply nosewheel shimmy vibration to pedals during heavy braking (MSFS only).

        Telemetry:
            Read: IsTaildragger  - bool; effect skipped entirely for taildragger aircraft
                  Brakes         - Union[List[float], Tuple[float,...]] ([left, right], 0.0–1.0);
                                    average of both axes drives shimmy intensity
                  SimOnGround    - int (0 or 1); effect suppressed when airborne (0)
                  WeightOnWheels - List[float] (compression 0.0–1.0); summed; must be
                                    non-zero to play
                  GroundSpeed    - float (m/s, ≥ 0); scales shimmy frequency between
                                    freq_lo (8 Hz) and freq_hi (16 Hz)
        """
        if not self._sim_is_msfs(): return
        if not self.is_pedals(): return
        if not self.nosewheel_shimmy: return
        if telem_data.IsTaildragger:
            return

        curve = 2.5
        # freq = 8
        freq_lo = 8
        freq_hi = 16
        brakes = telem_data.Brakes or (0, 0)
        on_ground = telem_data.SimOnGround or 0
        wow = sum(telem_data.WeightOnWheels or [0])
        if not wow or not on_ground:
            self.effects.dispose("nw_shimmy")
            return
        gs = telem_data.GroundSpeed or 0

        freq = int(utils.scale(gs, (self.nosewheel_shimmy_min_speed, self.nosewheel_shimmy_min_speed*3), (freq_lo, freq_hi)))
        logging.debug(f"brakes = {brakes}")
        avg_brakes = sum(brakes) / len(brakes)
        if avg_brakes >= self.nosewheel_shimmy_min_brakes and gs > self.nosewheel_shimmy_min_speed:
            shimmy = utils.non_linear_scaling(avg_brakes, self.nosewheel_shimmy_min_brakes, 1.0, curvature=curve) * self.nosewheel_shimmy_intensity
            logging.debug(f"Nosewheel Shimmy intensity calculation: (BrakesPct:{avg_brakes} | GS:{gs} | RT Intensity: {shimmy}")
            self.effects["nw_shimmy"].periodic(freq, shimmy, 90).start()
        else:
            self.effects.dispose("nw_shimmy")

    def on_telemetry(self, telem_data: BaseTelemetryData):
        super().on_telemetry(telem_data)
        self.msfs_update_nosewheel_shimmy(telem_data)