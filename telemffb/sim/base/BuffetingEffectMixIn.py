import random
import time

import telemffb.utils as utils
from telemffb.hw.ffb_rhino import EFFECT_TRIANGLE
from telemffb.sim.base.AircraftEffectUtilsBase import AircraftEffectUtilsBase
from telemffb.util.conversions import kt2ms
from telemffb.sim.BaseTelemetryData import BaseTelemetryData

class BuffetingEffectMixIn(AircraftEffectUtilsBase):
    aoa_buffet_freq = 13
    aoa_buffeting_enabled: bool = True
    buffeting_intensity : float = 0.2               # peak AoA buffeting intensity  0 to disable
    buffet_aoa : float          = 10.0              # AoA when buffeting starts (fallback)
    stall_aoa : float           = 15.0              # Stall AoA (fallback)
    # Renderer for the shake ("Buffet Style" setting): "Classic" is the
    # original steady single-frequency tone; "Dynamic" adds the gust-like
    # envelope + depth-dependent character below. Same estimator either way.
    # NOTE: Classic retires once Dynamic is field-validated — do not let it
    # become the codebase's third dead buffet implementation.
    stall_buffet_style: str = "Classic"

    # Dynamic-pressure ratio cap for the airflow factor: at the aircraft's own
    # 1g stall speed the factor is 1.0; an accelerated stall carries more q and
    # buffets HARDER, bounded here so a high-speed pull cannot slam the device.
    ACCEL_STALL_Q_CAP = 1.5

    # X-Plane band relative to the author-set warning alpha. The magnitude
    # ramp is ZERO at onset, so perceived buffet arrives partway into the
    # band — anchoring onset AT warn alpha put everything feelable well
    # after the horn (field test 2026-08). Straddling the horn instead
    # (onset below it, full above) gives a light pre-horn nibble that is
    # clearly building as the horn fires, matching how natural buffet
    # accompanies a stall warning.
    XP_WARN_ONSET_FRAC = 0.85    # onset at 85% of warn alpha
    XP_WARN_STALL_FRAC = 1.2     # full magnitude at 120% (estimated stall)

    # ---- Dynamic renderer character ------------------------------------
    # Real buffet is gusty: the envelope surges and ebbs irregularly rather
    # than shaking at constant amplitude, and the character gets heavier and
    # slower as separation deepens. The envelope is a random target in
    # [DYN_ENV_FLOOR, 1] re-picked every DYN_ENV_PERIOD-ish seconds and
    # low-pass chased, so magnitude wanders smoothly. Carrier frequency
    # slides from slider+ONSET_DELTA at first nibble to slider+DEEP_DELTA at full
    # stall (light high-frequency tickle -> heavy ragged shudder).
    # The envelope is MEAN-NEUTRAL (floor..peak averaging ~1.0): gusts surge
    # above the nominal level as often as they lull below it, so Dynamic's
    # AVERAGE strength equals Classic's at every AoA — the perceived onset
    # point stays where the threshold tuning put it. (The first cut used an
    # attenuate-only 0.35..1.0 envelope, which quietly re-introduced the
    # buffet-lags-the-horn problem the XP threshold fix had solved.)
    DYN_ENV_FLOOR = 0.5          # lull: never below half the nominal level
    DYN_ENV_PEAK = 1.5           # surge: gusts crest above nominal
    DYN_ENV_PERIOD = (0.3, 0.9)  # s; random re-pick interval (gust tempo)
    DYN_ENV_TAU = 0.25           # s; low-pass toward the target
    # Frequency anchors to the user's Buffeting Frequency slider
    # (aoa_buffet_freq) so the device-feel tuning applies in BOTH styles:
    # the sweep runs from slider+ONSET_DELTA at first nibble down to
    # slider+DEEP_DELTA at full stall (floored). At the default slider value
    # of 13 that is 14 -> 9 Hz. (16 at onset sat in the device's weak-feel
    # zone at small amplitudes.)
    DYN_FREQ_ONSET_DELTA = 1.0   # Hz above the slider at buffet onset
    DYN_FREQ_DEEP_DELTA = -4.0   # Hz below the slider at full stall
    DYN_FREQ_MIN = 4.0           # sweep floor for very low slider settings

    # NOTE (2026-08): TWO wing-drop asymmetry cues were built, field/
    # research-tested, and REMOVED — do not rebuild either naively:
    #  1. Directional bias of the buffet VIBRATION (jitter-cone steering,
    #     XP per-wing stall fractions / MSFS sideslip+roll-rate proxies):
    #     imperceptible even in a deliberately held skidding stall —
    #     vibration direction is a weak perceptual channel on this hardware.
    #  2. Constant-force lateral pull toward the drop ("aileron snatch"):
    #     mechanism-correct per the literature, but the documented cases are
    #     ICE-CONTAMINATED wings (ATR-72 Roselawn), spins, and uncertified
    #     types. Certified clean-wing GA is deliberately DESIGNED so this
    #     force does not occur at a normal stall (washout keeps ailerons in
    #     attached flow) — the real clean-stall lateral cue is force
    #     LIGHTENING/mush, not a pull. Shipping the pull as default-fleet
    #     realism was judged a dramatization and withdrawn.
    # Viable future directions if revisited: roll-axis force lightening as
    # separation develops (real on every reversible-control aircraft), or
    # the snatch pull gated on ICING telemetry (its documented home). The
    # XP plugin's StallFracL/R/StallFrac exports remain available for both.

    # Dynamic renders a TRIANGLE carrier (Classic keeps the default sine):
    # its odd harmonics read as ragged structural thudding rather than a
    # pure motor hum — the closest single waveform to broadband buffet.
    # Triangle RMS is ~18% below sine at equal peak, so magnitude is
    # compensated to keep the field-validated perceived levels (the
    # mean-neutrality lesson: character changes must not retune levels).
    DYN_WAVEFORM_RMS_COMP = 1.2

    def ac_update_buffeting(self, telem_data: BaseTelemetryData):
        """Update stall/AoA buffeting effect.

        AoA thresholds (onset .. full magnitude), per sim:
            MSFS:    StallAoA telemetry (STALL ALPHA, degrees) — onset at
                     50% of stall AoA, raised toward 70% with flaps; full at
                     the stall AoA itself.  MSFS knows the stall and
                     estimates the onset.
            XPLANE:  WarnAlpha telemetry (acf_stall_warn_alpha, degrees) —
                     the band STRADDLES the author-set stall-warning alpha:
                     onset at 85% of it, full magnitude at 120% (estimated
                     stall).  X-Plane knows the warning point and estimates
                     around it (there is no actual-stall-AoA dataref: stall
                     emerges per wing element from the airfoil polars).
            Neither: static class fallbacks (buffet_aoa / stall_aoa).

        Magnitude = airflow_factor x AoA ramp x buffeting_intensity, where
        airflow_factor is the DYNAMIC PRESSURE RATIO to the aircraft's own
        configuration-blended stall speed: (TAS / Vstall_cfg)^2, capped at
        ACCEL_STALL_Q_CAP.  At its own 1g stall every aircraft reaches
        factor 1.0 — a 45 kt trainer buffets as fully as a 130 kt jet (the
        old fixed 0..75 kt scale permanently muted slow aircraft at the one
        moment the effect exists for), and an accelerated stall at higher
        speed buffets harder, as it should.  Stall-speed reference:
            MSFS:    DesignSpeed (Vc, Vs0, Vs1) — Vs1 clean .. Vs0 full flap
            XPLANE:  Vs clean .. Vso landing configuration
            Neither: legacy linear 0..75 kt scale.

        Telemetry:
            Read:    AoA            - float (degrees); angle of attack
                     TAS            - float (m/s); scales magnitude via the
                                       q-ratio airflow factor
                     StallAoA       - float (degrees); MSFS stall AoA
                     WarnAlpha      - float (degrees); X-Plane author-set
                                       stall-warning alpha
                     Flaps          - Union[float, List[float]] (0.0-1.0);
                                       blends thresholds (MSFS) and the
                                       stall-speed reference (both sims)
                     DesignSpeed    - Tuple (Vc, Vs0, Vs1) m/s (MSFS)
                     Vs, Vso        - float (m/s); X-Plane stall speeds
                     WeightOnWheels - List[float]; any > 0 suppresses
            Written: _pct_max_stall_buffet (float, 0.0-1.0)
                     _buffeting            (float; raw magnitude)
        """
        if not self.buffeting_intensity or not self.aoa_buffeting_enabled:
            # Dispose on the way out or a mid-buffet disable leaves the last
            # periodic effect running until something else tears effects down.
            self.effects.dispose("buffeting")
            return

        aoa = telem_data.AoA or 0
        tas = telem_data.TAS or 0

        flaps = telem_data.Flaps or 0
        if isinstance(flaps, list):
            flaps = utils.average(flaps)
        flaps = utils.clamp(flaps, 0.0, 1.0)

        # ---- AoA thresholds ------------------------------------------------
        if telem_data.StallAoA:
            # MSFS: stall AoA known; onset estimated below it (flaps raise
            # the onset toward the stall: 50% clean -> 70% full flap).
            local_stall_aoa = telem_data.StallAoA
            local_buffet_aoa = local_stall_aoa * (0.5 + 0.2 * flaps)
        elif telem_data.WarnAlpha:
            # X-Plane: band straddles the author-set warning alpha (the
            # horn) — perceptible buffet builds as the horn fires rather
            # than lagging it (see XP_WARN_*_FRAC).
            local_buffet_aoa = telem_data.WarnAlpha * self.XP_WARN_ONSET_FRAC
            local_stall_aoa = telem_data.WarnAlpha * self.XP_WARN_STALL_FRAC
        else:
            local_stall_aoa = self.stall_aoa
            local_buffet_aoa = self.buffet_aoa

        if local_buffet_aoa <= 0 or local_stall_aoa <= local_buffet_aoa:
            self.effects.dispose("buffeting")
            return
        if aoa < local_buffet_aoa or max(telem_data.WeightOnWheels or [0]):
            self.effects.dispose("buffeting")
            return

        # ---- airflow factor: q relative to this aircraft's stall -----------
        vref = None
        ds = telem_data.DesignSpeed
        if ds:
            # (Vc, Vs0 full-flap stall, Vs1 clean stall) - m/s
            _vc, vs0, vs1 = ds
            if vs1:
                vref = vs1 + (vs0 - vs1) * flaps if vs0 else vs1
        elif telem_data.Vs:
            vs, vso = telem_data.Vs, telem_data.Vso
            vref = vs + (vso - vs) * flaps if vso else vs

        if vref and vref > 0:
            airflow_factor = utils.clamp((tas / vref) ** 2,
                                         0.0, self.ACCEL_STALL_Q_CAP)
        else:
            # No stall-speed telemetry: legacy fixed scale.
            airflow_factor = utils.scale_clamp(tas, (0, 75 * kt2ms), (0, 1.0))

        buffeting_factor = utils.scale_clamp(
            aoa, (local_buffet_aoa, local_stall_aoa), (0.0, 1.0))
        mag = utils.clamp(
            airflow_factor * buffeting_factor * self.buffeting_intensity,
            0.0, 1.0)
        telem_data._pct_max_stall_buffet = mag / self.buffeting_intensity

        # ---- renderer ------------------------------------------------------
        if self.stall_buffet_style == "Dynamic":
            freq, mag = self._dynamic_buffet_render(buffeting_factor, mag)
            # Single-axis devices feel only one direction (pedals: X = 90,
            # collective: Y = 0 — often mounted as a seat shaker). A random
            # direction there projects into ~10 Hz amplitude noise that
            # masks the gust envelope and delivers only partial strength,
            # so aim the carrier along the felt axis instead. Two-axis
            # joysticks keep the random walk (field-validated as is).
            if self.is_pedals():
                direction = 90
            elif self.is_collective():
                direction = 0
            else:
                direction = utils.RandomDirectionModulator
            self.effects["buffeting"].periodic(
                freq, mag, direction,
                effect_type=EFFECT_TRIANGLE).start()
        else:
            self.effects["buffeting"].periodic(
                self.aoa_buffet_freq, mag, utils.RandomDirectionModulator).start()

        telem_data._buffeting = mag  # save debug value

    def _dynamic_buffet_render(self, depth, mag):
        """Gust-like shaping for the Dynamic buffet style.

        ``depth`` is the AoA ramp position (0 onset .. 1 full stall);
        ``mag`` the estimator's magnitude. Returns (freq, shaped_mag): the
        magnitude rides a wandering mean-neutral envelope (irregular surges
        and lulls) and the carrier slides from a light high-frequency
        nibble at onset to a heavy, slower shudder deep in the stall.
        State survives across frames; it resets implicitly through the
        envelope chase when buffet re-enters.
        """
        now = time.perf_counter()
        if not hasattr(self, "_dyn_env"):
            self._dyn_env = 1.0
            self._dyn_env_target = 1.0
            self._dyn_next_pick = 0.0
            self._dyn_last_t = now
        if now >= self._dyn_next_pick:
            self._dyn_env_target = random.uniform(self.DYN_ENV_FLOOR,
                                                  self.DYN_ENV_PEAK)
            self._dyn_next_pick = now + random.uniform(*self.DYN_ENV_PERIOD)
        dt = utils.clamp(now - self._dyn_last_t, 0.0, 0.2)
        self._dyn_last_t = now
        # First-order chase toward the target (gusts build and relax,
        # never step).
        alpha = dt / self.DYN_ENV_TAU if self.DYN_ENV_TAU > 0 else 1.0
        self._dyn_env += (self._dyn_env_target - self._dyn_env) * \
            utils.clamp(alpha, 0.0, 1.0)
        f_onset = max(self.aoa_buffet_freq + self.DYN_FREQ_ONSET_DELTA,
                      self.DYN_FREQ_MIN)
        f_deep = max(self.aoa_buffet_freq + self.DYN_FREQ_DEEP_DELTA,
                     self.DYN_FREQ_MIN)
        freq = f_onset + (f_deep - f_onset) * utils.clamp(depth, 0.0, 1.0)

        # RMS compensation for the triangle carrier, then clamp — surges may
        # crest above the estimator level; keep the device sane.
        mag = mag * self._dyn_env * self.DYN_WAVEFORM_RMS_COMP
        return freq, utils.clamp(mag, 0.0, 1.0)

    def ac_update_drag_buffet(self, telem_data: BaseTelemetryData, type: str):
        drag_buffet_threshold = 100  # indicated TAS via telemetry
        tas = telem_data.TAS or 0
        if tas < drag_buffet_threshold:
            return 0

    def on_telemetry(self, telem_data: BaseTelemetryData):
        super().on_telemetry(telem_data)
        self.ac_update_buffeting(telem_data)
