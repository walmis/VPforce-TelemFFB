import logging

import telemffb.utils as utils
from telemffb.util.conversions import kt2ms
from telemffb.sim.base.AircraftEffectUtilsBase import AircraftEffectUtilsBase
from telemffb.sim.BaseTelemetryData import BaseTelemetryData

class ElevatorDroopEffectMixIn(AircraftEffectUtilsBase):
    elevator_droop_enabled: bool = False
    elevator_droop_force: float = 0.0

    '''Mixin for elevator droop effect.'''
    def ac_override_elevator_droop(self, telem_data: BaseTelemetryData):
        """Apply elevator droop constant force when below 20 kt (simulates gravity-droop at low speed).

        Telemetry:
            Read: TAS - float (m/s, ≥ 0); true airspeed; force linearly scales from
                         elevator_droop_force at 0 m/s down to 0 at 20 kt (~10.3 m/s)
        """
        if not self.is_joystick():
            return
        if not self.elevator_droop_enabled or not self.elevator_droop_force:
            self.effects.dispose('elev_droop')
            return

        if telem_data['TAS'] < 20 * kt2ms:
            force = utils.scale_clamp(telem_data['TAS'], (20 * kt2ms, 0), (0, self.elevator_droop_force))
            self.effects['elev_droop'].constant(force, 180).envelope(
             attackFromForce=0,       # Start from zero for noticeable fade-in
             attackTime=1000,          # 1000ms attack
         ).start()
            logging.debug(f"override elevator:{force}")
        else:
            self.effects.dispose('elev_droop')

    def on_telemetry(self, telem_data: BaseTelemetryData):
        super().on_telemetry(telem_data)
        if self.is_joystick():
            self.ac_override_elevator_droop(telem_data)