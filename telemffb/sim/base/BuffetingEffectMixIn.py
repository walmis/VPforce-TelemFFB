import telemffb.utils as utils
from telemffb.sim.base.AircraftEffectUtilsBase import AircraftEffectUtilsBase
from telemffb.util.conversions import kt2ms
from telemffb.sim.BaseTelemetryData import BaseTelemetryData

class BuffetingEffectMixIn(AircraftEffectUtilsBase):
    aoa_buffet_freq = 13
    aoa_buffeting_enabled: bool = True
    buffeting_intensity : float = 0.2               # peak AoA buffeting intensity  0 to disable
    buffet_aoa : float          = 10.0              # AoA when buffeting starts (fallback)
    stall_aoa : float           = 15.0              # Stall AoA (fallback)

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
        freq = self.aoa_buffet_freq
        mag = utils.clamp(
            airflow_factor * buffeting_factor * self.buffeting_intensity,
            0.0, 1.0)
        telem_data._pct_max_stall_buffet = mag / self.buffeting_intensity
        self.effects["buffeting"].periodic(
            freq, mag, utils.RandomDirectionModulator).start()

        telem_data._buffeting = mag  # save debug value

    def ac_update_drag_buffet(self, telem_data: BaseTelemetryData, type: str):
        drag_buffet_threshold = 100  # indicated TAS via telemetry
        tas = telem_data.TAS or 0
        if tas < drag_buffet_threshold:
            return 0

    def on_telemetry(self, telem_data: BaseTelemetryData):
        super().on_telemetry(telem_data)
        self.ac_update_buffeting(telem_data)
