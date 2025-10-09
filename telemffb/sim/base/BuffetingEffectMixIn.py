import telemffb.utils as utils
from telemffb.sim.base.AircraftEffectUtilsBase import AircraftEffectUtilsBase
from telemffb.util.conversions import kt2ms

class BuffetingEffectMixIn(AircraftEffectUtilsBase):
    aoa_buffet_freq = 13
    aoa_buffeting_enabled: bool = True
    buffeting_intensity : float = 0.2               # peak AoA buffeting intensity  0 to disable
    buffet_aoa : float          = 10.0              # AoA when buffeting starts
    stall_aoa : float           = 15.0              # Stall AoA

    def _ac_calc_buffeting(self, aoa, speed, telem_data) -> tuple:
        """Calculate buffeting amount and frequency

        :param aoa: Angle of attack in degrees
        :type aoa: float
        :param speed: Airspeed in m/s
        :type speed: float
        :return: Tuple (freq_hz, magnitude)
        :rtype: tuple
        """
        stall_buffet_threshold_percent = 110


        if self._sim_is_msfs():
            local_stall_aoa = telem_data.get("StallAoA", 0)   # Get stall AoA telemetry from MSFS
            local_buffet_aoa = local_stall_aoa * (stall_buffet_threshold_percent/100)
        elif self._sim_is_xplane():
            local_buffet_aoa = telem_data.get("WarnAlpha", 0)
            local_stall_aoa = local_buffet_aoa * 1.25
        else:
            local_stall_aoa = self.stall_aoa
            local_buffet_aoa = self.buffet_aoa

        if not self.buffeting_intensity:
            return (0, 0)
        max_airflow_speed = 75*kt2ms  # speed at which airflow_factor is 1.0
        airflow_factor = utils.scale_clamp(speed, (0, max_airflow_speed), (0, 1.0))
        buffeting_factor = utils.scale_clamp(aoa, (local_buffet_aoa, local_stall_aoa), (0.0, 1.0))
        # todo calc frequency
        return (self.aoa_buffet_freq, airflow_factor * buffeting_factor * self.buffeting_intensity)

    def ac_update_buffeting(self, telem_data: dict):
        if not self.buffeting_intensity or not self.aoa_buffeting_enabled:
            return

        aoa = telem_data.get("AoA", 0)
        tas = telem_data.get("TAS", 0)

        max_airflow_speed = 75*kt2ms  # speed at which airflow_factor is 1.0

        ds = telem_data.get("DesignSpeed", None)
        if ds:
            stall_aoa = telem_data.get("StallAoA", None)
            #vc - This design constant represents the aircraft ideal cruising speed
            #vs0 - This design constant represents the the stall speed when flaps are fully extended
            #vs1 - This design constant represents the stall speed when flaps are fully retracted
            vc, vs0, vs1 = ds
            #max_airflow_speed = vc

        local_stall_aoa = telem_data.get("StallAoA", None)
        if local_stall_aoa is not None:
            flaps = telem_data.get("Flaps", 0)

            if isinstance(flaps, list):
                flaps = utils.average(telem_data.get("Flaps", 0)) * 0.2 # flaps down increases stall threshold by 20%
            else:
                flaps = telem_data.get("Flaps", 0) * 0.2
            stall_buffet_threshold_percent = 0.5 + flaps
            local_buffet_aoa = local_stall_aoa * stall_buffet_threshold_percent
        else:
            local_stall_aoa = self.stall_aoa
            local_buffet_aoa = self.buffet_aoa

        if aoa < local_buffet_aoa:
            self.effects.dispose("buffeting")
            return
        if local_buffet_aoa == 0 or local_stall_aoa == 0:
            return
        if max(telem_data.get('WeightOnWheels', 0)):
            self.effects.dispose("buffeting")
            return

        airflow_factor = utils.scale_clamp(tas, (0, max_airflow_speed), (0, 1.0))
        buffeting_factor = utils.scale_clamp(aoa, (local_buffet_aoa, local_stall_aoa), (0.0, 1.0))
        # todo calc frequency
        freq = self.aoa_buffet_freq
        # return (13.0, airflow_#factor * buffeting_factor * self.buffeting_intensity)
        # freq, mag = self._calc_buffeting(aoa, tas, telem_data)
        # manage periodic effect for buffeting
        mag = airflow_factor * buffeting_factor * self.buffeting_intensity
        pct_max_stall_buffet = mag / self.buffeting_intensity
        telem_data['_pct_max_stall_buffet'] = pct_max_stall_buffet
        #logging.debug(f"Buffeting: {mag}")
        self.effects["buffeting"].periodic(freq, mag, utils.RandomDirectionModulator).start()
        # effects["buffeting2"].periodic(freq, mag, 45, phase=120).start()

        telem_data["_buffeting"] = mag  # save debug value

    def ac_update_drag_buffet(self, telem_data: dict, type: str):
        drag_buffet_threshold = 100  # indicated TAS via telemetry
        tas = telem_data.get("TAS", 0)
        if tas < drag_buffet_threshold:
            return 0
        
    def on_telemetry(self, telem_data: dict):
        super().on_telemetry(telem_data)
        self.ac_update_buffeting(telem_data)
