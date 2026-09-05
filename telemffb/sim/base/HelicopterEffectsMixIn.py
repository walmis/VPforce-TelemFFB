import logging
import math

import telemffb.utils as utils
from telemffb.SettingsManager import SpringModeEnum
from telemffb.hw.ffb_rhino import EFFECT_SAWTOOTHDOWN, EFFECT_SQUARE, HapticEffect
from telemffb.sim.base.AdvancedSpringMixIn import AdvancedSpringMixIn
from telemffb.sim.BaseTelemetryData import BaseTelemetryData
from telemffb.util.conversions import FFB_UNITS

perftracker = utils.PerformanceTracker()

class HelicopterEffectsMixIn(AdvancedSpringMixIn):
    """Mixin for helicopter-specific configuration and effects."""

    # user parameters
    rotor_blade_count: int = 2

    # Engine/rotor rumble
    engine_rotor_rumble_enabled: bool = False
    heli_engine_rumble_intensity: float = 0.12

    # ETL / Overspeed
    etl_effect_enable: bool = True
    etl_start_speed: float = 6.0
    etl_stop_speed: float = 22.0
    etl_effect_intensity: float = 0.2
    etl_shake_frequency: float = 14.0

    overspeed_effect_enable: bool = True
    overspeed_shake_start: float = 70.0
    overspeed_shake_intensity: float = 0.2
    overspeed_shake_frequency: float = 0.0

    # Blade slap (blade-vortex interaction)
    blade_slap_enable: bool = False
    blade_slap_intensity: float = 0.15
    blade_slap_use_native: bool = True    # XPLANE only: sim-computed signal, exclusively
    blade_slap_band_center: float = 32.4  # m/s (63 kt); heuristic speed-band peak
    blade_slap_g_factor: float = 0.8      # weight of maneuvering load in the inferred signal

    # Band edges as ratios of the center: at the 63 kt default this spans
    # ~19-107 kt, matching the original fixed band.  Scaling with the center
    # keeps the response shape consistent and out of the hover regime when a
    # user tunes the band down for a lightly-loaded rotor.
    BLADE_SLAP_BAND_LO = 0.31
    BLADE_SLAP_BAND_HI = 1.69

    # Vortex Ring State (VRS)
    vrs_effect_enable: bool = False
    vrs_effect_intensity: float = 0.0
    vrs_threshold_speed: float = 0.0
    vrs_vs_onset: float = 0
    vrs_vs_max: float = 0

    # Collective force trim override settings
    collective_ft_ovd_enabled: bool = False
    collective_ft_ovd_release: int = 0
    collective_ft_ovd_spring_gain: float = 0.5
    collective_ft_ovd_tr_damper: float = 0.05
    collective_ft_ovd_reset: int = 0
    collective_ft_ovd_trim_rate: int = 200
    collective_ft_init: bool = False
    collective_ft_ovd_trim_down = 0
    collective_ft_ovd_trim_up = 0
    collective_ft_ovd_cp0_y: float = 1.0
    collective_ft_use_master_buttons: bool = False
    # end of user parameters
    

    ########################################
    ######                            ######
    ######     Helicopter Effects     ######
    ######                            ######
    ########################################

    def ac_calc_etl_effect(self, telem_data: BaseTelemetryData, blade_ct=None):
        """Apply ETL (Effective Translational Lift) and rotor overspeed shake effects.

        Telemetry:
            Read: N              - str; aircraft model name; used to special-case
                                    UH-60L WoW logic (tailwheel always positive)
                  TAS            - float (m/s, ≥ 0); true airspeed; compared against
                                    etl_start/stop_speed and overspeed_shake_start
                  WeightOnWheels - List[float] ([nose/left, left/right, right/tail],
                                    compression 0.0–1.0); sum > 0 suppresses effect
            Read (XPLANE): PropRPM  - Union[float, List[float]] (RPM); index [0] taken;
                                       used to compute etl_shake_frequency
            Read (others): RotorRPM - Union[float, List[float]] (RPM); list max taken;
                                       used to compute etl_shake_frequency
        """
        #  rotor = 245
        mod = telem_data.N
        tas = telem_data.TAS or 0
        WoW = sum(telem_data.WeightOnWheels or [0, 0, 0])
        if mod == "UH-60L":
            # UH60 always shows positive value for tailwheel
            wow_list = telem_data.WeightOnWheels or [0, 0, 0]
            WoW = wow_list[0] + wow_list[2]

        if self._sim_is_xplane():
            rotor = telem_data.PropRPM or 0
            if isinstance(rotor, list):
                rotor = rotor[0]
        else:
            rotor = telem_data.RotorRPM or 0
            if isinstance(rotor, list):
                rotor = max(rotor)
        if WoW > 0:
            # logging.debug("On the Ground, moving forward. Probably on a Ship! - Dont play effect!")
            self.effects.dispose("etlX", "etlY", "overspeedX", "overspeedY")
            return
        if blade_ct is None:
            blade_ct = 2
            rotor = 250

        self.etl_shake_frequency = (rotor / 75) * blade_ct
        self.overspeed_shake_frequency = self.etl_shake_frequency * 0.75

        etl_mid = (self.etl_start_speed + self.etl_stop_speed) / 2.0

        if (
            (tas >= self.etl_start_speed and tas <= self.etl_stop_speed)
            and self.etl_effect_intensity
            and self.etl_effect_enable
        ):
            shake = self.etl_effect_intensity * utils.gaussian_scaling(
                tas, self.etl_start_speed, self.etl_stop_speed, peak_percentage=0.5, curve_width=0.7
            )
            shake = utils.clamp(shake, 0.0, 1.0)
            self.effects["etlY"].periodic(self.etl_shake_frequency, shake, 0).start()
            self.effects["etlX"].periodic(self.etl_shake_frequency + 4, shake, 90).start()
            logging.debug(f"Playing ETL shake (freq = {self.etl_shake_frequency}, intens= {shake})")
        else:
            self.effects.dispose("etlX", "etlY")

        if tas >= self.overspeed_shake_start and self.overspeed_effect_enable:
            shake = self.overspeed_shake_intensity * utils.non_linear_scaling(
                tas, self.overspeed_shake_start, self.overspeed_shake_start + 15, curvature=0.7
            )
            shake = utils.clamp(shake, 0.0, 1.0)
            self.effects["overspeedY"].periodic(self.overspeed_shake_frequency, shake, 0).start()
            self.effects["overspeedX"].periodic(self.overspeed_shake_frequency + 4, shake, 90).start()
            logging.debug(f"Overspeed shake (freq = {self.etl_shake_frequency}, intens= {shake}) ")
        else:
            self.effects.dispose("overspeedX", "overspeedY")

    def ac_update_vrs_effect(self, telem_data: BaseTelemetryData):
        """Apply Vortex Ring State buffet when descending slowly with low forward speed.

        Telemetry:
            Read: VerticalSpeed    - float (m/s); positive = climb, negative = descent;
                                      effect only active when VS < 0 (descending) and
                                      |VS| ≥ vrs_vs_onset
                  TAS              - float (m/s, ≥ 0); true airspeed; effect suppressed
                                      when spd > vrs_threshold_speed
                  WeightOnWheels   - List[float] (compression 0.0–1.0); max() > 0
                                      suppresses effect (on ground)
            Read (DCS only): TAS   - adj_TAS = TAS − |VerticalSpeed| used as spd proxy
            Written (DCS only): _adj_TAS (float, m/s; adjusted TAS for DCS path)
        """
        vs = telem_data.VerticalSpeed or 0
        if self._sim_is_dcs():
            # spd = abs(telem_data.VlctVectors[0])
            tas = telem_data.TAS
            adj_tas = tas - abs(vs)
            spd = adj_tas
            telem_data._adj_TAS = adj_tas
        else:
            spd = abs(telem_data.TAS or 0)
        wow = max(telem_data.WeightOnWheels or [1])
        # print(f"tas:{tas}, vs:{vs}, wow:{wow}")
        if not self.vrs_effect_enable or wow or spd > self.vrs_threshold_speed or vs > 0:
            # print("I'm out")
            self.effects.dispose("vrs_buffet", "vrs_buffet2")
            return

        if abs(vs) >= self.vrs_vs_onset:
            vs_factor = utils.scale(abs(vs), (self.vrs_vs_onset, self.vrs_vs_max), (0.0, self.vrs_effect_intensity))
            if spd == 0:
                spd_factor = 1
            else:
                spd_factor = utils.scale(spd, (spd * 1.2, spd), (0, 1))

            intensity = utils.clamp(vs_factor * spd_factor, 0, 1)

            self.effects["vrs_buffet"].periodic(10, intensity, utils.RandomDirectionModulator).start()
            self.effects["vrs_buffet2"].periodic(12, intensity, utils.RandomDirectionModulator).start()
        else:
            self.effects.dispose("vrs_buffet", "vrs_buffet2")

    def ac_update_heli_engine_rumble(self, telem_data: BaseTelemetryData, blade_ct=None):
        """Apply rotor/engine rumble for helicopters.

        Telemetry:
            Read (XPLANE): PropRPM  - Union[float, List[float]] (RPM, ≥ 0); index [0] taken;
                                       used to derive rumble frequency
            Read (others): RotorRPM - Union[float, List[float]] (RPM, ≥ 0); list max taken;
                                       used to derive rumble frequency; effect suppressed below 5 RPM
            Read: N      - str; aircraft model name; NOT USED in calculation
                  TAS    - float (m/s, ≥ 0); true airspeed; NOT USED in calculation
                  EngRPM - Union[float, List[float]] (RPM, ≥ 0); list max taken;
                            rumble suppressed when zero

        Note: N and TAS are fetched into local variables but neither influences
              the effect output in the current implementation.
        """
        if not self.engine_rotor_rumble_enabled or not self.heli_engine_rumble_intensity:
            self.effects.dispose("rotor_rpm0-1", "rotor_rpm1-1")
            return
        if self._sim_is_xplane():
            rrpm = telem_data.PropRPM or 0
            if isinstance(rrpm, list):
                rrpm = rrpm[0]
        else:
            rrpm = telem_data.RotorRPM or 0
            if isinstance(rrpm, list):
                rrpm = max(rrpm)
        mod = telem_data.N
        tas = telem_data.TAS or 0
        eng_rpm = telem_data.EngRPM or 0
        if isinstance(eng_rpm, list):
            eng_rpm = max(eng_rpm)

        # rotor = telem_data.get("RotorRPM")

        if blade_ct is None:
            blade_ct = 2
            rrpm = 250

        if rrpm < 5:
            self.effects.dispose("rotor_rpm0-1", "rotor_rpm1-1")
            return

        logging.debug(f"Engine Rumble: Blade_Ct={blade_ct}, RPM={rrpm}")
        frequency = float(rrpm) / 45 * blade_ct

        median_modulation = 2
        frequency2 = frequency + median_modulation
        if frequency > 0 and eng_rpm > 0:
            logging.debug(f"Current Heli Engine Rumble Intensity = {self.heli_engine_rumble_intensity}")
            self.effects["rotor_rpm0-1"].periodic(
                frequency, self.heli_engine_rumble_intensity * 0.5, 0
            ).start()  # vib on X axis
            self.effects["rotor_rpm1-1"].periodic(
                frequency2, self.heli_engine_rumble_intensity * 0.5, 90
            ).start()  # vib on Y axis
        else:
            self.effects.dispose("rotor_rpm0-1", "rotor_rpm1-1")

    def ac_collective_force_trim_override(self, telem_data: BaseTelemetryData, spring):
        """Generic effect enabling spring force and hardware trim for collective axis.

        Telemetry:
            Read:    WeightOnWheels - List[float] ([nose/left, left/right, right/tail],
                                      compression 0.0–1.0); summed; informational only —
                                      does not currently gate the effect
                     ForceTrimSW    - bool; cockpit force-trim switch state; when False
                                      the spring follows the stick position without locking
            Written: _coll_ft_dt      (float, s; frame delta time)
                     _coll_ft_step    (float; normalized trim step per frame)
                     _coll_ft_trim_pos (float, -1.0 to 1.0; normalized trim offset)
        """

        if not self.is_collective():
            return
        if not self.spring_mode_is(SpringModeEnum.FORCETRIM):
            # If feature disabled, ensure spring is stopped and abort
            self.effects["collective_ft"].stop()
            return
        if not self.collective_ft_ovd_release:
            # Force trim on the collective is unusable without a release
            # button (unlike pedals, where an unbound button is a valid
            # configuration). Flagged per-frame so the message persists while
            # the condition exists and clears once a button is bound.
            self.flag_error("Collective force trim enabled but the trim release button is not configured")
            return

        dt = perftracker.get_time_delta("collective_ft_perf")
        self.telem_data._coll_ft_dt = dt

        wow = sum(telem_data.WeightOnWheels or [1])

        input_data = HapticEffect.get_device_input()
        _, y = self._get_device_axes()
        # No live device: buttons unreadable, none can be pressed.
        current_buttons = input_data.getPressedButtons() if input_data is not None else ()

        force_trim_active = telem_data.get("ForceTrimSW", True)
        if force_trim_active is None:
            force_trim_active = True

        if not force_trim_active:
            # Force trim is enabled, but the 'ForceTrimSW' flag is false, just move
            self.spring_y.set_coefficient(self.collective_ft_ovd_tr_damper)
            self.collective_ft_ovd_cp0_y = float(utils.clamp(y, -1.0, 1.0))
            self.spring_y.set_offset(self.collective_ft_ovd_cp0_y)
            spring.setCondition(self.spring_y)
            return

        # decide what to do depending on which button is pressed
        if self.check_button_press(self.collective_ft_ovd_release, self.collective_ft_use_master_buttons):
            # use spring force as dampening.  Configured damper value applied as spring gain.  cpO will follow stick
            # as it is moved while spring force is enabled.
            # return from method so default spring gains do not get applied at the end of the method
            self.spring_y.set_coefficient(self.collective_ft_ovd_tr_damper)

            self.collective_ft_ovd_cp0_y = float(utils.clamp(y, -1.0, 1.0))
            self.spring_y.set_offset(self.collective_ft_ovd_cp0_y)
            spring.setCondition(self.spring_y)
            spring.start(override=True)
            return

        elif self.check_button_press(self.collective_ft_ovd_reset, self.collective_ft_use_master_buttons):
            # if trim reset button pressed, set offsets back to 0
            # print("TRIM RESET")
            self.collective_ft_ovd_cp0_y = 1.0
            self.spring_y.set_offset(self.collective_ft_ovd_cp0_y)
            spring.setCondition(self.spring_y)

        # calculate step size based on configured rate and delta time
        trim_step_size = self.collective_ft_ovd_trim_rate * dt / FFB_UNITS

        self.telem_data._coll_ft_step = trim_step_size

        if self.check_button_press(self.collective_ft_ovd_trim_down, self.collective_ft_use_master_buttons):
            # shift offset based on previously calculated step size.  Ensure value does not exceed limits
            # print("TRIM DOWN")
            self.collective_ft_ovd_cp0_y += trim_step_size
            self.collective_ft_ovd_cp0_y = utils.clamp(
                self.collective_ft_ovd_cp0_y, -1.0, 1.0)
            self.spring_y.set_offset(self.collective_ft_ovd_cp0_y)
        elif self.check_button_press(self.collective_ft_ovd_trim_up, self.collective_ft_use_master_buttons):
            # shift offset based on previously calculated step size.  Ensure value does not exceed limits
            # print("TRIM UP")
            self.collective_ft_ovd_cp0_y -= trim_step_size
            self.collective_ft_ovd_cp0_y = utils.clamp(
                self.collective_ft_ovd_cp0_y, -1.0, 1.0)
            self.spring_y.set_offset(self.collective_ft_ovd_cp0_y)

        self.telem_data._coll_ft_trim_pos = self.collective_ft_ovd_cp0_y

        # If trim release is not pressed, set spring gain based on user setting and start spring override
        self.spring_y.set_coefficient(self.collective_ft_ovd_spring_gain)

        spring.setCondition(self.spring_y)
        # ensure spring is started with override = true
        spring.start(override=True)

    def _blade_slap_signal(self, telem_data: BaseTelemetryData) -> float:
        """Blade-vortex-interaction intensity, 0..1.

        On X-Plane with blade_slap_use_native enabled, the
        sim-computed signal (rotor_blade_slap_rat via the plugin) drives the
        effect exclusively.  Everywhere else, and on X-Plane with the toggle
        off, the signal is inferred from wake geometry: BVI happens when the rotor
        flies through its own wake — moderate forward speed on a shallow
        descent gradient, plus flares and loaded turns pushing the wake back
        into the disc.

        Telemetry:
            Read: BladeSlap     - float (0..1, XPLANE plugin); native signal
                  IAS           - float (m/s); speed band peaks at blade_slap_band_center
                  VerticalSpeed - float (m/s); descent angle band peaks ~6 deg
                  G             - float (MSFS/XP); loaded-flare/turn contribution
                  ACCs          - List[float] (g, DCS/IL2); index [1] = normal
                                  load factor, used when G is absent
            Written: _blade_slap_src ("native" | "inferred"; active source)
        """
        if self._sim_is_xplane() and self.blade_slap_use_native:
            telem_data._blade_slap_src = "native"
            return utils.clamp(telem_data.get("BladeSlap", 0) or 0, 0.0, 1.0)
        telem_data._blade_slap_src = "inferred"

        ias = telem_data.IAS or 0
        if ias < 5.0:
            return 0.0
        center = self.blade_slap_band_center or 32.4
        speed_band = utils.gaussian_scaling(
            ias, center * self.BLADE_SLAP_BAND_LO, center * self.BLADE_SLAP_BAND_HI,
            peak_percentage=0.5, curve_width=0.8)

        vs = telem_data.VerticalSpeed or 0
        descent_deg = math.degrees(math.atan2(-vs, ias))
        # Both gates must pass: the angle band (wake stays in the disc plane)
        # AND a genuine sink rate.  At low band speeds a 1 deg "descent" is
        # only ~50-150 fpm — the transient sink of an accelerating nose-down
        # attitude — which is not wake re-entry and must stay silent.
        if vs < -1.0 and 1.0 < descent_deg < 14.0:
            descent_term = utils.gaussian_scaling(descent_deg, 0.0, 12.0,
                                                  peak_percentage=0.5, curve_width=0.5)
        else:
            descent_term = 0.0

        # G source differs per sim: MSFS/XP report G directly; DCS/IL2 ship
        # the body acceleration vector (ACCs, normal axis at [1]) instead.
        g_load = telem_data.G
        if g_load is None:
            accs = telem_data.ACCs
            if isinstance(accs, (list, tuple)) and len(accs) > 1:
                g_load = accs[1]
        g_term = utils.clamp(((g_load or 1.0) - 1.15) * 1.5, 0.0, 1.0)

        return utils.clamp(
            speed_band * (descent_term + self.blade_slap_g_factor * g_term), 0.0, 1.0)

    def ac_update_blade_slap(self, telem_data: BaseTelemetryData, blade_ct=None):
        """Blade-slap kicks through the controls: a sharp sawtooth periodic at
        blade-passage rate, gated by the BVI signal.  Two-bladed teetering
        rotors slap loudest (blade-count character scale).

        Telemetry:
            Read: WeightOnWheels - List[float]; sum > 0 suppresses the effect
                  PropRPM[0] (XPLANE) / RotorRPM (others) - rev rate for the
                  blade-passage frequency
            Written: _blade_slap_sig (float; debug - current signal value)
        """
        if not (self.blade_slap_enable and self.blade_slap_intensity):
            self.effects.dispose("blade_slap_x", "blade_slap_y")
            return
        if sum(telem_data.WeightOnWheels or [0, 0, 0]) > 0:
            self.effects.dispose("blade_slap_x", "blade_slap_y")
            return

        if self._sim_is_xplane():
            rotor = telem_data.PropRPM or 0
            if isinstance(rotor, list):
                rotor = rotor[0]
        else:
            rotor = telem_data.RotorRPM or 0
            if isinstance(rotor, list):
                rotor = max(rotor)
        blade_ct = blade_ct or 2
        freq = (rotor / 60.0) * blade_ct

        sig = self._blade_slap_signal(telem_data)
        telem_data._blade_slap_sig = sig
        if freq < 1.0 or sig < 0.05:
            self.effects.dispose("blade_slap_x", "blade_slap_y")
            return

        mag = self.blade_slap_intensity * sig
        # two-bladed rotors are the wop-wop kings; soften for higher counts
        mag *= utils.clamp(2.0 / blade_ct, 0.6, 1.0)
        mag = utils.clamp(mag, 0.0, 1.0)

        self.effects["blade_slap_y"].periodic(freq, mag, 0, effect_type=EFFECT_SAWTOOTHDOWN).start()
        self.effects["blade_slap_x"].periodic(freq, mag, 90, effect_type=EFFECT_SAWTOOTHDOWN).start()

    def on_telemetry(self, telem_data: BaseTelemetryData):
        super().on_telemetry(telem_data)
        if self.is_helicopter():
            self.ac_calc_etl_effect(telem_data, blade_ct=self.rotor_blade_count)
            self.ac_update_heli_engine_rumble(telem_data, blade_ct=self.rotor_blade_count)
            self.ac_update_vrs_effect(telem_data)
            self.ac_update_blade_slap(telem_data, blade_ct=self.rotor_blade_count)
    
