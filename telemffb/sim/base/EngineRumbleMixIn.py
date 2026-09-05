import telemffb.utils as utils
from telemffb.hw.ffb_rhino import EFFECT_TRIANGLE
from telemffb.sim.base.AircraftEffectUtilsBase import AircraftEffectUtilsBase


import logging
from telemffb.sim.BaseTelemetryData import BaseTelemetryData


class EngineRumbleMixIn(AircraftEffectUtilsBase):
    """Mixin to contain engine rumble related configuration, state and handlers.

    Moves piston/prop and rotor/jet engine rumble attrs and methods out of
    AircraftBase so per-instance state and behavior live with the mixin.
    """
    engine_jet_rumble_enabled: bool = False  # Engine Rumble - Jet specific
    engine_prop_rumble_enabled: bool = True  # Engine Rumble - Piston specific - based on Prop RPM
    engine_rotor_rumble_enabled: bool = False  # Engine Rumble - Helicopter specific - based on Rotor RPM

    engine_rumble_intensity: float = 0.02
    engine_rumble_lowrpm: int = 450
    engine_rumble_lowrpm_intensity: float = 0.12
    engine_rumble_highrpm: int  = 2800
    engine_rumble_highrpm_intensity: float = 0.06

    afterburner_effect_intensity = 0.0      # peak intensity for afterburner rumble effect
    jet_engine_rumble_intensity = 0      # peak intensity for jet engine rumble effect
    jet_engine_rumble_freq = 45             # base frequency for jet engine rumble effect (Hz)

    afterburner_effect_enabled: bool = True

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # No per-instance runtime state currently required for engine rumble;
        # placeholder in case future smoothing/filters are needed.
        self._engine_rumble_last = None

    def ac_update_piston_engine_rumble(self, telem_data: BaseTelemetryData):
        """Apply piston/propeller engine rumble based on RPM.

        Telemetry:
            Read (MSFS/XPLANE): PropRPM   - Union[float, List[float]] (RPM, ≥ 0);
                                             list max taken for multi-engine aircraft;
                                             effect suppressed below 5 RPM
            Read (DCS):         ActualRPM - Union[float, List[float]] (RPM, ≥ 0);
                                             absolute RPM (not percentage)
            Read (IL2):         RPM       - Union[float, List[float]] (RPM, ≥ 0)
        """
        if not self.engine_prop_rumble_enabled:
            self.effects.dispose("prop_rpm0-1", "prop_rpm0-2", "prop_rpm1-1", "prop_rpm1-2")
            return

        if self._sim_is('DCS'):
            rpm = telem_data.ActualRPM or 0.0
        elif self._sim_is('MSFS') or self._sim_is_xplane():
            rpm = telem_data.PropRPM or 0.0
        elif self._sim_is('IL2'):
            rpm = telem_data.RPM or 0.0
        else:
            logging.warning("Unknown sim trying to play Engine Rumble effect")
            rpm = 0.0

        if type(rpm) == list:
            rpm = max(rpm)

        if rpm < 5:
            self.effects.dispose("prop_rpm0-1", "prop_rpm0-2", "prop_rpm1-1", "prop_rpm1-2")
            return

        frequency = float(rpm) / 60

        median_modulation = 2
        modulation_pos = 2
        modulation_neg = 1
        frequency2 = frequency + median_modulation

        # The detuned twins tenderize the low end but curse the high end:
        # each pair (tone + equal-amplitude twin) beats at FULL depth
        # (0..2x) regardless of how small the detune is, so at cruise any
        # detune at all reads as a slow wah-wah undertone (field reports
        # 2026-08 — first the fixed +-3 Hz sweep, then, with only the
        # sweep RATE tapered, a slower but still full-depth beat). Both
        # the detune (beat rate) AND the twin amplitude (beat depth) now
        # taper inversely with frequency above the user's Low RPM point:
        # identity at and below it (the chunky recip throb at idle is
        # untouched), fading to a steady hum at cruise. The main tones
        # get an RMS compensation as the twins fade so the felt level
        # tracks the intensity taper — the factor is exactly 1.0 at full
        # depth, leaving the low end bit-identical.
        f_ref = max(self.engine_rumble_lowrpm, 60) / 60.0
        detune_scale = utils.clamp(f_ref / frequency, 0.0, 1.0)
        beat_depth = detune_scale ** 2

        r1_modulation = utils.sine_point_in_time(3 * detune_scale, 10000)
        r2_modulation = utils.sine_point_in_time(3 * detune_scale, 17500, phase_offset_deg=45)

        if frequency > 0:
            force_limit = max(self.engine_rumble_highrpm_intensity, self.engine_rumble_lowrpm_intensity)
            dynamic_rumble_intensity = utils.clamp(self.ac_calc_engine_intensity(rpm), 0, force_limit)
            logging.debug(f"Current Engine Rumble Intensity = {dynamic_rumble_intensity}")

            main_mag = utils.clamp(
                dynamic_rumble_intensity * (2.0 - beat_depth ** 2) ** 0.5, 0.0, 1.0)
            twin_mag = utils.clamp(dynamic_rumble_intensity * beat_depth, 0.0, 1.0)

            self.effects["prop_rpm0-1"].periodic(frequency, main_mag, 0).start()
            self.effects["prop_rpm0-2"].periodic(frequency + r1_modulation, twin_mag, 0).start()
            self.effects["prop_rpm1-1"].periodic(frequency2, main_mag, 90).start()
            self.effects["prop_rpm1-2"].periodic(frequency2 + r2_modulation, twin_mag, 90).start()
        else:
            self.effects.dispose("prop_rpm0-1", "prop_rpm0-2", "prop_rpm1-1", "prop_rpm1-2")

    def ac_calc_engine_intensity(self, rpm) -> float:
        """
        Calculate the intensity to use based on the configurable high and low intensity settings and high and low RPM settings
        intensity will decrease from max to min settings as the RPM increases from min to max settings
        lower RPM = more rumble effect
        """
        min_rpm = self.engine_rumble_lowrpm
        max_rpm = self.engine_rumble_highrpm
        max_intensity = self.engine_rumble_lowrpm_intensity
        min_intensity = self.engine_rumble_highrpm_intensity

        rpm_percentage = 1 - ((rpm - min_rpm) / (max_rpm - min_rpm))

        if rpm < min_rpm:
            # give some extra juice if RPM is very low (i.e. on engine start)
            interpolated_intensity = utils.scale(rpm, (0, min_rpm), (max_intensity * 2, max_intensity))
        else:
            # update to use scaling function
            interpolated_intensity = utils.scale(rpm, (min_rpm, max_rpm), (max_intensity, min_intensity))

        logging.debug(
            f"rpm = {rpm} | rpm percent of range: {rpm_percentage} | interpolated intensity: {interpolated_intensity}"
        )

        return interpolated_intensity

    def ac_update_jet_engine_rumble(self, telem_data: BaseTelemetryData):
        """Apply jet engine rumble scaled by engine power.

        Telemetry:
            Read (XPLANE): EngPCT - Union[float, List[float]] (%, 0–100); engine power
                                     percentage; list max taken; effect suppressed at 0
            Read (others): EngRPM - Union[float, List[float]] (% of max RPM, 0–100);
                                     list max taken; used to scale frequency and intensity
        """
        if not self.engine_jet_rumble_enabled or not self.jet_engine_rumble_intensity > 0:
            self.effects.dispose("je_rumble_1_1", "je_rumble_1_2", "je_rumble_2_1", "je_rumble_2_2")
            return

        frequency = self.jet_engine_rumble_freq
        median_modulation = 10
        modulation_pos = 3
        modulation_neg = 3
        frequency2 = frequency + median_modulation
        precision = 2
        effect_index = 4
        phase_offset = 120
        if self._sim_is_xplane():
            jet_eng_rpm = telem_data.EngPCT or 0
        else:
            jet_eng_rpm = telem_data.EngRPM or 0
        if type(jet_eng_rpm) == list:
            jet_eng_rpm = max(jet_eng_rpm)

        if jet_eng_rpm == 0:
            # logging.debug(f"Both Less: Eng1: {eng1} Eng2: {eng2}, effect= {Aircraft.effect_index_set}")
            self.effects.dispose("je_rumble_1_1", "je_rumble_1_2", "je_rumble_2_1", "je_rumble_2_2")
            return

        r1_modulation = utils.sine_point_in_time(3, 60000)
        r2_modulation = utils.sine_point_in_time(2, 42500, phase_offset_deg=0)
        intensity = self.jet_engine_rumble_intensity * (jet_eng_rpm / 100)
        intensity = utils.clamp(intensity, 0, 1)
        rt_freq = round(frequency + (10 * (jet_eng_rpm / 100)), 4)
        rt_freq2 = round(rt_freq + median_modulation, 4)
        self.effects["je_rumble_1_1"].periodic(rt_freq + r1_modulation, intensity, 0, effect_index).start()
        # effects["je_rumble_1_2"].periodic(rt_freq + r1_modulation, intensity, 0, effect_index).start()
        self.effects["je_rumble_2_1"].periodic(rt_freq2 + r2_modulation, intensity, 90, effect_index, phase=phase_offset).start()
        # effects["je_rumble_2_2"].periodic(rt_freq2 + r2_modulation, intensity, 90, effect_index, phase=phase_offset+30).start()
        logging.debug(f"JE-M1={r1_modulation}, F1-1={rt_freq}, F1-2={round(rt_freq + r1_modulation,4)} | JE-M2 = {r2_modulation}, F2-1={rt_freq2}, F2-2={round(rt_freq2 + r2_modulation, 4)} ")

    def ac_update_ab_effect(self, telem_data: BaseTelemetryData):
        """Apply afterburner rumble effect.

        Telemetry:
            Read: Afterburner - Union[float, List[float]] (bool-like 0 or 1, or 0.0–1.0
                                 scaled for BMS); MSFS = per-engine bool; list max taken;
                                 effect plays only when non-zero
        """
        if not self.afterburner_effect_intensity or not self.afterburner_effect_enabled:
            self.effects.dispose("ab_rumble_1_1", "ab_rumble_2_1")
            return

        frequency = 20
        median_modulation = 2
        modulation_pos = 2
        modulation_neg = 1
        frequency2 = frequency + median_modulation
        precision = 2
        afterburner_pos = telem_data.Afterburner or 0
        if isinstance(afterburner_pos, list):
            afterburner_pos = max(afterburner_pos)

        r1_modulation = utils.sine_point_in_time(modulation_pos, 15000)
        r2_modulation = utils.sine_point_in_time(modulation_neg, 15000)

        if afterburner_pos and (self.anything_has_changed("Afterburner", afterburner_pos) or self.anything_has_changed("Modulation", r1_modulation)):
            # logging.debug(f"AB Effect Updated: LT={Left_Throttle}, RT={Right_Throttle}")
            intensity = utils.clamp(self.afterburner_effect_intensity * afterburner_pos, 0, 1)
            self.effects["ab_rumble_1_1"].periodic(frequency + r1_modulation, intensity, 0,effect_type=EFFECT_TRIANGLE ).start()
            # effects["ab_rumble_1_2"].periodic(frequency + r1_modulation, intensity, 0).start()
            self.effects["ab_rumble_2_1"].periodic(frequency + r1_modulation, intensity, 45,effect_type=EFFECT_TRIANGLE ).start()
            # effects["ab_rumble_2_2"].periodic(frequency2 + r2_modulation, intensity, 45, 4, phase=120,
            #                                   offset=60).start()
            # logging.debug(f"AB-Modul1= {r1_modulation} | AB-Modul2 = {r2_modulation}")
        elif afterburner_pos == 0:
            # logging.debug(f"Both Less: Eng1: {eng1} Eng2: {eng2}, effect= {Aircraft.effect_index_set}")
            self.effects.dispose("ab_rumble_1_1")
            # effects.dispose("ab_rumble_1_2")
            self.effects.dispose("ab_rumble_2_1")

    def on_telemetry(self, telem_data: BaseTelemetryData):
        super().on_telemetry(telem_data)
        self.ac_update_jet_engine_rumble(telem_data)
        
        if self.is_jet_aircraft():
            self.ac_update_ab_effect(telem_data)

        elif self.is_propeller_aircraft():
            self.ac_update_piston_engine_rumble(telem_data)