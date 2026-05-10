import logging

import telemffb.utils as utils
from telemffb.sim.base.AircraftEffectUtilsBase import AircraftEffectUtilsBase
from telemffb.sim.BaseTelemetryData import BaseTelemetryData

perftracker = utils.PerformanceTracker()

class DecelerationEffectMixIn(AircraftEffectUtilsBase):
    """Mixin for deceleration/decel related configuration, runtime state and handler.

    Moves the deceleration effect behavior out of AircraftBase so per-instance
    state (last speed etc.) is initialised here and the AircraftBase remains a
    thin delegator.
    """
    # Beta effects - set to 1 to enable
    deceleration_effect_enable = 0
    deceleration_effect_enable_areyoureallysure = 0
    deceleration_max_force = 0.5
    decel_scale_factor = 1
    decel_invert_force = False
    decel_airborne_disable: bool = True

    def __init__(self, *args, **kwargs):
        # cooperative init for mixin ordering
        super().__init__(*args, **kwargs)
        # per-instance runtime state used by decel effect
        self.last_speed = None
        self.last_y_gs = 0

    def ac_update_decel_effect(self, telem_data: BaseTelemetryData):
        """Apply forward (deceleration) constant force to joystick.

        Telemetry:
            Read (all paths):  WeightOnWheels - List[float] ([nose, left, right], compression
                                                0.0–1.0); summed; selects airborne vs ground mode
                               TAS            - float (m/s, ≥ 0); true airspeed; effect
                                                suppressed when zero; DCS airborne path
                                                uses rate-of-change to derive accel_g
            Read (MSFS):       AccBody[2]     - float (g); longitudinal body-frame acceleration
            Read (XPLANE):     Gaxil          - float (g); axial G; negated for sign convention
            Read (DCS/IL2/BMS on ground): ACCs[0] - float (g); lateral G vector component
            Read (DCS airborne only):     speedbrakes_value - float (0.0–1.0); scales
                                           airborne decel force magnitude
            Written: decel_g        (float, g; instantaneous decel G, DCS airborne path only)
                     decel_g_smooth (float, g; 8-sample smoothed decel G)
        """
        if not self.deceleration_effect_enable or not self.is_joystick():
            self.effects.dispose("decel")
            return

        wow = sum(telem_data.WeightOnWheels or [0, 0, 0], 0)
        if not wow and self.decel_airborne_disable:
            # When off ground, dispose effect and return
            self.effects.dispose('decel')
            return

        y_gs = 0
        last_y_gs = 0

        if self._sim_is("DCS") or self._sim_is("IL2") or self._sim_is("BMS"):
            if self.decel_airborne_disable:
                # We are on the ground, calculate using G vectors
                y_gs = (telem_data.ACCs or [0, 0, 0])[0]
                last_y_gs = (self._last_telem_data.ACCs or [0, 0, 0])[0]
            else:
                # we are in the air, calculate G vector from rate of change of velocity since DCS Y g vector is world orientation

                dt = perftracker.get_time_delta('decel')
                speed = telem_data.TAS

                if not hasattr(self, 'last_speed'):
                    self.last_speed = speed
                if not hasattr(self, 'last_y_gs'):
                    self.last_y_gs = 0
                last_speed = self.last_speed
                self.last_speed = speed

                accel_g = 0
                if last_speed is not None and dt > 0:
                    delta_v = speed - last_speed
                    acceleration = delta_v / dt  # m/s²
                    accel_g = acceleration / 9.81  # convert to Gs

                self.telem_data.decel_g = accel_g

                y_gs = accel_g
                last_y_gs = self.last_y_gs
                self.last_y_gs = y_gs

        elif self._sim_is("MSFS"):
            y_gs = telem_data.AccBody[2]
            last_y_gs = (self._last_telem_data.AccBody or [0, 0, 0])[2]

        elif self._sim_is_xplane():
            y_gs = -telem_data.Gaxil
            last_y_gs = self._last_telem_data.Gaxil or 0
        delta_y = abs(y_gs) - abs(last_y_gs)

        if not self.anything_has_changed("decel", y_gs):
            return

        if abs(delta_y) > 3:  # If the per-frame rate of change is greater than 3 Gs, we have likely crashed and telemetry is violently spiking.. do not play effect:
            return

        if not (telem_data.TAS or 0):
            self.effects.dispose("decel")
            return
        avg_y_gs = self.smoother.get_average("y_gs", y_gs, sample_size=8)

        self.telem_data.decel_g_smooth = avg_y_gs

        max_gs = self.deceleration_max_force

        dir = 180 if not self.decel_invert_force else 0

        if (avg_y_gs < -0.03 < 500):  # Don't play effect for very small, or very large (crash) force values, or no weight on wheels
            if abs(avg_y_gs) > max_gs:
                avg_y_gs = -max_gs

            avg_y_gs = utils.clamp(abs(avg_y_gs) * self.decel_scale_factor, 0, 1)
            if self._sim_is_dcs() and not wow:
                sb = telem_data.speedbrakes_value
                avg_y_gs = avg_y_gs * sb
            logging.debug(f"y_gs = {y_gs} avg_y_gs = {avg_y_gs}")
            self.effects["decel"].constant(abs(avg_y_gs), direction=dir).start()
        else:
            self.effects.dispose("decel")

    def on_telemetry(self, telem_data: BaseTelemetryData):
        super().on_telemetry(telem_data)
        self.ac_update_decel_effect(telem_data)