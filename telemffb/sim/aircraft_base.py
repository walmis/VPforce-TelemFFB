#
# This file is part of the TelemFFB distribution (https://github.com/walmis/TelemFFB).
# Copyright (c) 2023 Valmantas Palikša.
# Copyright (c) 2023 Micah Frisby
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, version 3.
#
# This program is distributed in the hope that it will be useful, but
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU
# General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <http://www.gnu.org/licenses/>.
#


import logging
import time
import math
import random
from collections import deque
from typing import override
from telemffb import telem
import telemffb.utils as utils
from telemffb.sim.base.ElevatorDroopEffectMixIn import ElevatorDroopEffectMixIn

from telemffb.sim.base.WindEffectMixIn import WindEffectMixIn
from telemffb.sim.base.AdvancedSpringMixIn import AdvancedSpringMixIn
from telemffb.sim.base.DecelerationEffectMixIn import DecelerationEffectMixIn
from telemffb.sim.base.EngineRumbleMixIn import EngineRumbleMixIn
from telemffb.sim.base.MotionEffectsMixIn import MotionEffectsMixIn
from telemffb.sim.base.WeaponsEffectMixIn import WeaponsEffectMixIn
from telemffb.sim.base.BuffetingEffectMixIn import BuffetingEffectMixIn
from telemffb.sim.base.HelicopterEffectsMixIn import HelicopterEffectsMixIn
from telemffb.sim.base.DeadzoneMixIn import DeadzoneMixIn
from telemffb.sim.base.HydraulicLossMixIn import HydraulicLossMixIn
from telemffb.sim.base.PedalSpringOverrideMixIn import PedalSpringOverrideMixIn

from telemffb.hw.ffb_rhino import (
    HapticEffect,
    FFBReport_SetCondition,
    EFFECT_SPRING,
    EFFECT_DAMPER,
    EFFECT_INERTIA,
    EFFECT_FRICTION,
    EFFECT_SPRING_ADJUSTER,
)
import telemffb.globals as G
from telemffb.sim.BaseTelemetryData import BaseTelemetryData

# by accessing effects dict directly new effects will be automatically allocated
# example: effects["myUniqueName"]
effects: utils.Dispenser = utils.Dispenser(HapticEffect)


def use_shaker_backend() -> None:
    """Rebind effects.cls (and the module-level HapticEffect / FFBReport_SetCondition)
    to the bass-shaker facade.

    Called from main.py after _setup_device_configuration() when
    G.device_type == 'shaker'. The reason this is a runtime swap rather than a
    conditional ``import`` at module load time is that aircraft_base.py is
    imported transitively from main.py's top-level imports (via MainWindow),
    which runs *before* G.device_type is assigned.

    Concrete aircraft modules (aircrafts_msfs_xp / aircrafts_dcs / aircrafts_il2)
    each `from telemffb.hw.ffb_rhino import HapticEffect` at their own module-load
    time, so they capture an independent reference. Rebinding the global in this
    module is not enough — we also reach into ``sys.modules`` and rewrite each
    sibling's binding so subsequent ``HapticEffect()`` calls use the shaker facade.
    """
    import sys

    global HapticEffect, FFBReport_SetCondition
    from telemffb.hw.ffb_shaker import (
        HapticEffect as _S_HapticEffect,
        FFBReport_SetCondition as _S_Cond,
    )
    HapticEffect = _S_HapticEffect
    FFBReport_SetCondition = _S_Cond
    effects.cls = _S_HapticEffect

    for mod_name in ('telemffb.sim.aircrafts_msfs_xp',
                     'telemffb.sim.aircrafts_dcs',
                     'telemffb.sim.aircrafts_il2'):
        mod = sys.modules.get(mod_name)
        if mod is None:
            continue
        if hasattr(mod, 'HapticEffect'):
            mod.HapticEffect = _S_HapticEffect
        if hasattr(mod, 'FFBReport_SetCondition'):
            mod.FFBReport_SetCondition = _S_Cond


def use_router_backend() -> None:
    """Rebind effects.cls to the router-aware HapticEffect facade.

    Called from main.py after the EffectRouter is initialised, but only for
    FFB device children (joystick / pedals / collective / trimwheel /
    rudder). The shaker child continues to use ``use_shaker_backend()`` —
    the router does not own the audio mixing path.

    The mechanism mirrors ``use_shaker_backend``: aircraft_base is imported
    transitively before G.device_type is set, so we re-bind the effect
    class at runtime — both here and on the sibling sim modules that
    captured their own ``HapticEffect`` reference at import time.
    """
    import sys

    global HapticEffect, FFBReport_SetCondition
    from telemffb.routing.ffb_router import (
        HapticEffect as _R_HapticEffect,
        FFBReport_SetCondition as _R_Cond,
    )
    HapticEffect = _R_HapticEffect
    FFBReport_SetCondition = _R_Cond
    effects.cls = _R_HapticEffect

    for mod_name in ('telemffb.sim.aircrafts_msfs_xp',
                     'telemffb.sim.aircrafts_dcs',
                     'telemffb.sim.aircrafts_il2'):
        mod = sys.modules.get(mod_name)
        if mod is None:
            continue
        if hasattr(mod, 'HapticEffect'):
            mod.HapticEffect = _R_HapticEffect
        if hasattr(mod, 'FFBReport_SetCondition'):
            mod.FFBReport_SetCondition = _R_Cond

# Highpass filter dispenser
HPFs: utils.Dispenser = utils.Dispenser(utils.HighPassFilter)

# Lowpass filter dispenser
LPFs: utils.Dispenser = utils.Dispenser(utils.LowPassFilter)

perftracker = utils.PerformanceTracker()

# unit conversions (to m/s)
knots = 0.514444
kmh = 1.0 / 3.6
deg = math.pi / 180
fpss2gs = 1 / 32.17405

EFFECT_SQUARE = 3
EFFECT_SINE = 4
EFFECT_TRIANGLE = 5
EFFECT_SAWTOOTHUP = 6
EFFECT_SAWTOOTHDOWN = 7


class _RunwayPeakDetector:
    """Edge-triggered peak detector for the nose-wheel HPF stream.

    Fires once per discrete bump: arms when ``|sample| >= threshold``,
    tracks the running maximum until the signal falls below
    ``threshold * release_ratio``, then emits the captured peak amplitude
    on release. ``refractory_s`` blocks re-arm immediately after a fire so
    a single bump produces a single thump.

    The output is a deterministic function of the input sample stream and
    perf_counter timestamps — same telemetry sequence yields the same fires.
    Critical for muscle memory: the user's brain learns "joint at this
    speed = thump of this amplitude" and the haptic side honours that
    consistently.
    """
    __slots__ = ("threshold", "release_ratio", "refractory_s",
                 "_armed", "_peak", "_last_fire_t")

    def __init__(self, threshold: float = 0.12,
                 release_ratio: float = 0.5,
                 refractory_s: float = 0.10):
        self.threshold = threshold
        self.release_ratio = release_ratio
        self.refractory_s = refractory_s
        self._armed = False
        self._peak = 0.0
        self._last_fire_t = -1e9

    def update(self, sample: float, now: float):
        """Feed one sample; return the peak amplitude if a bump just fired,
        else ``None``."""
        a = abs(sample)
        if not self._armed:
            if a >= self.threshold and (now - self._last_fire_t) >= self.refractory_s:
                self._armed = True
                self._peak = a
            return None
        if a > self._peak:
            self._peak = a
        if a < self.threshold * self.release_ratio:
            fire_amp = self._peak
            self._armed = False
            self._peak = 0.0
            self._last_fire_t = now
            return fire_amp
        return None


class AircraftBase(
    PedalSpringOverrideMixIn,
    HelicopterEffectsMixIn,
    WeaponsEffectMixIn,
    DeadzoneMixIn,
    HydraulicLossMixIn,
    DecelerationEffectMixIn,
    EngineRumbleMixIn,
    WindEffectMixIn,
    AdvancedSpringMixIn,
    MotionEffectsMixIn,
    BuffetingEffectMixIn,
    ElevatorDroopEffectMixIn,
):
    """Base class for all aircraft types, providing common functionality and state management."""

    rotor_blade_count = 2

    cpO_x = 0
    cpO_y = 0
    aoa_buffet_freq = 13

    buffeting_intensity: float = 0.2  # peak AoA buffeting intensity  0 to disable
    buffet_aoa: float = 10.0  # AoA when buffeting starts
    stall_aoa: float = 15.0  # Stall AoA
    aoa_effect_enabled: bool = False

    runway_rumble_intensity: float = 1.0  # peak runway intensity, 0 to disable
    runway_rumble_enabled: bool = True

    keep_forces_on_pause: bool = True
    enable_damper_ovd: bool = False
    damper_force: float = 0
    enable_inertia_ovd: bool = False
    inertia_force: float = 0
    enable_friction_ovd: bool = False
    friction_force: float = 0

    speedbrake_motion_intensity : float = 0.12      # peak vibration intensity when speed brake is moving, 0 to disable
    speedbrake_buffet_intensity : float = 0.15      # peak buffeting intensity when speed brake deployed,  0 to disable
    speedbrake_speed_thresh :   float = 80 * 0.514444  # speed threshold for speedbrake to start buffeting

    spoiler_motion_intensity: float = 0.0  # peak vibration intensity when spoilers is moving, 0 to disable
    spoiler_buffet_intensity: float = 0.15  # peak buffeting intensity when spoilers deployed,  0 to disable
    spoiler_spd_thresh_low: float = 80 * 0.514444  # speed threshold for spoilers to start buffeting
    spoiler_spd_thresh_hi: float = 140 * 0.514444  # speed threshold for spoilers to stop buffeting

    aoa_buffeting_enabled: bool = True
    buffeting_intensity : float = 0.2               # peak AoA buffeting intensity  0 to disable
    buffet_aoa : float          = 10.0              # AoA when buffeting starts
    stall_aoa : float           = 15.0              # Stall AoA
    wind_effect_enabled : int = 0
    wind_effect_scaling: int = 0
    wind_effect_max_intensity: int = 0
    aoa_effect_gain: float = 1.0
    uncoordinated_turn_effect_enabled: int = 1

    afterburner_effect_intensity = 0.0      # peak intensity for afterburner rumble effect
    jet_engine_rumble_intensity = 0      # peak intensity for jet engine rumble effect
    jet_engine_rumble_freq = 45             # base frequency for jet engine rumble effect (Hz)

    ###
    ### AoA reduction force effect
    ###
    aoa_reduction_effect_enabled = 0
    aoa_reduction_max_force = 0.0
    critical_aoa_start = 22
    critical_aoa_max = 25

    # gforce_effect_master: bool = False
    # gforce_effect_enable: bool = False
    gforce_effect_invert_force = 0  # case where "180" degrees does not equal "away from pilot"
    gforce_effect_curvature = 2.2
    gforce_effect_max_intensity = 1.0
    gforce_min_gs = 1.5  # G's where the effect starts playing
    gforce_max_gs = 5.0  # G limit where the effect maxes out at strength defined in gforce_effect_max_intensity
    # gforce_effect_advanced_enabled = False
    gforce_effect_advanced_curve = {}
    gforce_current_factor: float = 0.0

    # new_gforce_effect_enable = False
    new_gforce_effect_center_deadzone = 0
    new_gforce_min_gs = 1.1  # G's where the effect starts playing
    new_gforce_max_gs = 5.0  # G limit where the effect maxes out at strength defined in gforce_effect_max_intensity
    new_gforce_effect_deflection_factor = 1.0
    new_gforce_enable_neg_gs = False
    new_gforce_min_gs_neg = 0.9
    new_gforce_max_gs_neg = -4
    new_gforce_effect_deflection_factor_neg = 1.0

    gear_motion_effect_enabled: bool = True
    gear_motion_intensity: float = 0.12
    gear_buffet_effect_enabled: bool = True
    gear_buffet_intensity: float = 0.15     # peak buffeting intensity when gear down during flight,  0 to disable

    ####
    #### Beta effects - set to 1 to enable
    deceleration_effect_enable = 0
    deceleration_effect_enable_areyoureallysure = 0
    deceleration_max_force = 0.5
    decel_scale_factor = 1
    decel_invert_force = False
    decel_airborne_disable: bool = True
    ###

    enable_hydraulic_loss_effect: bool = False
    hydraulic_loss_threshold: float = 0.95
    hydraulic_loss_damper: float = 1
    hydraulic_loss_inertia: float = 1
    hydraulic_loss_friction: float = 1

    damper_coeff: int = 0
    inertia_coeff: int = 0
    friction_coeff: int = 0

    runway_rumble_intensity: float = 1.0  # peak runway intensity, 0 to disable
    runway_rumble_enabled: bool = True
    gun_vibration_intensity: float = 0.12  # peak gunfire vibration intensity, 0 to disable
    cm_vibration_intensity: float = 0.12  # peak countermeasure release vibration intensity, 0 to disable
    weapon_release_intensity: float = 0.12  # peak weapon release vibration intensity, 0 to disable
    weapon_effect_direction: int = 45  # Affects the direction of force applied for gun/cm/weapon release effect, Set to -1 for random direction

    engine_jet_rumble_enabled: bool = False  # Engine Rumble - Jet specific
    engine_prop_rumble_enabled: bool = True  # Engine Rumble - Piston specific - based on Prop RPM
    engine_rotor_rumble_enabled: bool = False  # Engine Rumble - Helicopter specific - based on Rotor RPM

    engine_rumble_intensity: float = 0.02
    engine_rumble_lowrpm: int = 450
    engine_rumble_lowrpm_intensity: float = 0.12
    engine_rumble_highrpm: int  = 2800
    engine_rumble_highrpm_intensity: float = 0.06

    # Phase-locked impulse synthesis. "physics" fires one trigger_pulse per
    # blade-pass / cylinder-firing crossing of an integrated phase on the
    # shaker; "synthesis" is the legacy continuous-sine path. The Rhino
    # backend degrades to a periodic at the same carrier frequency
    # (rpm/60 * divisions), but only when the routing pack declares a
    # ``type:joystick`` layer for the voice — see ``prop_phys_*`` /
    # ``cyl_phys_*`` / ``rotor_phys_*`` in ``effect_routes_default.json``.
    engine_rumble_mode: str = "physics"
    cylinder_firing_enabled: bool = False  # piston cylinder-firing voice
    cylinder_count: int = 4
    is_4_stroke: bool = True
    prop_blade_count: int = 2
    prop_reduction_ratio: float = 1.0
    tailrotor_enabled: bool = False
    tailrotor_blade_count: int = 2
    tailrotor_gear_ratio: float = 4.5  # tail-rotor RPM = main * ratio
    # Vertical-speed-driven landing impulse. Magnitudes sit between gentle and
    # hard touchdown VS in m/s.
    touchdown_vs_enabled: bool = False
    touchdown_vs_gentle: float = 0.5  # m/s — barely felt
    touchdown_vs_hard: float = 4.0    # m/s — full magnitude
    touchdown_vs_min_amp: float = 0.15
    touchdown_vs_max_amp: float = 1.0

    # gforce_effect_enable : bool = False

    flaps_motion_intensity : float = 0.12      # peak vibration intensity when flaps are moving, 0 to disable
    flaps_buffet_intensity : float = 0.0      # peak buffeting intensity when flaps are deployed,  0 to disable

    canopy_motion_intensity : float = 0.12      # peak vibration intensity when canopy is moving, 0 to disable
    canopy_buffet_intensity : float = 0.0      # peak buffeting intensity when canopy is open during flight,  0 to disable

    max_aoa_cf_force: float = 0.2  # CF force sent to device at %stall_aoa
    elevator_droop_enabled: bool = False
    elevator_droop_force: float = 0.0
    aircraft_is_fbw: bool = False           #deprecated

    gear_motion_effect_enabled: bool = False
    gear_buffet_effect_enabled: bool = False
    gear_buffet_freq = 10
    gear_buffet_speed_low = 100
    gear_buffet_speed_high = 150
    speedbrake_motion_effect_enabled: bool = False
    speedbrake_buffet_effect_enabled: bool = False
    flaps_motion_effect_enabled: bool = False
    canopy_motion_effect_enabled: bool = False
    spoiler_motion_effect_enabled: bool = False
    spoiler_buffet_effect_enabled: bool = False

    tailhook_motion_effect_enabled: bool = False
    tailhook_motion_intensity: float = 0.12

    fuelboom_motion_effect_enabled: bool = False
    fuelboom_motion_intensity: float = 0.12

    wingfold_motion_effect_enabled: bool = False
    wingfold_motion_intensity: float = 0.10

    weapon_release_effect_enabled: bool = False
    weapon_release_intensity : float = 0.12         # peak weapon release vibration intensity, 0 to disable
    weapon_effect_direction: int = 45               # Affects the direction of force applied for gun/cm/weapon release effect, Set to -1 for random direction

    runway_rumble_intensity : float = 1.0           # peak runway intensity, 0 to disable
    runway_rumble_enabled: bool = False
    # Spatial runway rumble: nose-wheel HPF stream is rendered live on the
    # stick (front) and on the shaker (rear) delayed by wheelbase / ground
    # speed, so a runway joint reads as front-then-back. Override
    # `aircraft_wheelbase` per-aircraft if 8 m is far off (XML/per-aircraft
    # config). Speed floor avoids absurd delays at near-stationary taxi.
    aircraft_wheelbase: float = 8.0                 # nose-to-main wheelbase in metres
    runway_spatial_min_speed_kt: float = 5.0        # ground-speed floor for delay calc
    # Rolling-RMS thresholds that govern the smooth-vs-rough blend on the
    # shaker. Below `_smooth_rms` the shaker reads the fully-delayed front
    # signal; above `_rough_rms` it reads the live signal (delay collapses
    # because grass/dirt has no spatially coherent joints to chase).
    runway_spatial_smooth_rms: float = 0.05
    runway_spatial_rough_rms: float = 0.20

    touchdown_effect_enabled: bool = False
    touchdown_effect_max_force: float = 0.5
    touchdown_effect_max_gs: float = 3.0

    gunfire_effect_enabled: bool = False
    gun_vibration_intensity : float = 0.12          # peak gunfire vibration intensity, 0 to disable
    countermeasure_effect_enabled: bool = False
    cm_vibration_intensity : float = 0.12           # peak countermeasure release vibration intensity, 0 to disable

    afterburner_effect_enabled: bool = True

    etl_effect_enable: bool = True
    overspeed_effect_enable: bool = True
    vrs_effect_enable: bool = False
    vrs_effect_intensity: float = 0.0
    vrs_threshold_speed: float = 0.0
    vrs_vs_onset: float = 0
    vrs_vs_max: float = 0

    #spring_mode = G.JoystickSpringMode.BASIC

    ## 0=DCS Default | 1=spring disabled (Heli)), 2=spring enabled at %100 (FW)
    #pedal_spring_mode = G.PedalSpringMode.STATIC

    aircraft_vs_speed = 87
    aircraft_vs_gain = 0.25
    aircraft_vne_speed = 435
    aircraft_vne_gain = 1.0

    pedals_init = 0
    pedal_spring_coeff_x = 0
    last_pedal_x = 0
    pedal_trimming_enabled = False
    pedal_spring_gain = 1.0
    pedal_dampening_gain = 0

    pedal_force_trim_enabled: bool = False
    pedal_ft_use_master_buttons: bool = False
    pedal_ft_release_button: int = 0
    pedal_ft_reset_button: int = 0
    pedal_ft_damper_enabled: bool = False
    pedal_ft_damper_force: float = 0.0
    pedal_trim_reset_complete: bool = False

    etl_start_speed = 6.0 # m/s
    etl_stop_speed = 22.0 # m/s
    etl_effect_intensity = 0.2 # [ 0.0 .. 1.0]
    etl_shake_frequency = 14.0 # value has been deprecated in favor of rotor RPM calculation
    overspeed_shake_start = 70.0 # m/s
    overspeed_shake_intensity = 0.2
    heli_engine_rumble_intensity = 0.12

    collective_ft_ovd_enabled: bool = False
    collective_ft_ovd_release: int = 0
    collective_ft_ovd_spring_gain: float = 0.5
    collective_ft_ovd_tr_damper: float = 0.05
    collective_ft_ovd_reset: int = 0
    collective_ft_ovd_trim_rate: int = 200
    collective_ft_init: bool = False
    collective_ft_ovd_trim_down = 0
    collective_ft_ovd_trim_up = 0
    collective_ft_ovd_cp0_y = 4096
    collective_ft_use_master_buttons: bool = False

    adv_spr_override_enabled: bool = False   #deprecated
    adv_spr_gains: str = 'none'
    adv_spr_use_hardware_trim: bool = False
    # adv_spr_use_game_trim: bool = True
    gforce_effect_adv_curve: str = 'none'
    trimwheel_elev_up_button: int = 0
    trimwheel_elev_dn_button: int = 0
    trimwheel_use_master_buttons: bool = False
    trimwheel_axis_invert: bool = False
    trimwheel_use_axis: bool = False

    override_spring_trim_down: int = 0
    override_spring_trim_left: int = 0
    override_spring_trim_up: int = 0
    override_spring_trim_right: int = 0
    override_spring_trim_rate: int = 200
    override_spring_cp0_x: int = 0
    override_spring_cp0_y: int = 0

    enable_deadzone: bool = False
    deadzone_base_pct: float = 0.0

    g_y_offset: int = 0

    last_device_x = None
    last_device_y = None

    smoother = utils.Smoother()
    _ipc_telem = {}
    stepper_dict = {}

    @property
    def telem_data(self):
        return self._telem_data

    def __init__(self, name: str, **kwargs):
        super().__init__()
        self._name = name
        self._changes = {}
        self._change_counter = {}
        self._telem_data = BaseTelemetryData()
        self._last_telem_data = BaseTelemetryData()
        self._ipc_telem = {}
        # Per-instance ring buffer for nose-wheel HPF magnitude history,
        # consumed by ac_update_runway_rumble to render the shaker's
        # spatially-delayed copy of the front rumble. Sized for ~4 s at
        # 30 Hz which covers slow-taxi wheelbase delays even on long
        # aircraft. Each entry is a (perf_counter_seconds, value) tuple.
        self._rumble_v1_buffer: deque = deque(maxlen=128)
        # Rolling absolute v1 magnitudes used to estimate surface roughness:
        # smooth tarmac with discrete joints reads as low RMS, grass/dirt
        # reads as continuous high RMS. The roughness factor is then used
        # to blend the shaker between purely-delayed (smooth) and
        # mostly-live (rough) so the spatial illusion is not drowned out
        # by broadband suspension noise. ~1 s window at 30 Hz telemetry.
        self._rumble_v1_rms_buffer: deque = deque(maxlen=30)
        # Independent peak detectors per channel: front (live HPF, stick) vs
        # rear (delayed HPF, shaker). Each fires a one-shot ``runway_impulse``
        # / ``runway_impulse_delayed`` when its own stream peaks, so a single
        # runway joint reads as front-thump-then-rear-thump with the
        # wheelbase / ground-speed delay between them.
        self._runway_peak_front = _RunwayPeakDetector()
        self._runway_peak_rear = _RunwayPeakDetector()
        self.adv_g_settings_dict: dict = {}
        self.adv_spr_settings_dict: dict = {}
        self.active_deadzone_pct: float = 0.0
        self.deadzone_active = False

        # clear any existing effects
        self.effects.clear()

    def _sample_rumble_buffer_at(self, target_t: float) -> float:
        """Linear-interpolate a value from the v1 ring buffer at target_t.

        Returns 0 if the buffer is empty or target_t predates the oldest
        sample (typical at startup or after a long airborne stretch). The
        stick reads the live HPF value; this lookup feeds the shaker so the
        rear rumble lags the front by wheelbase / ground-speed seconds.
        """
        buf = self._rumble_v1_buffer
        if not buf:
            return 0.0
        if target_t <= buf[0][0]:
            return 0.0
        if target_t >= buf[-1][0]:
            return buf[-1][1]
        # Linear scan from the right: typical lookups land on the recent
        # tail, so this is O(delay/dt) ~ a handful of comparisons.
        prev_t, prev_v = buf[-1]
        for t, v in reversed(buf):
            if t <= target_t:
                if prev_t == t:
                    return v
                frac = (target_t - t) / (prev_t - t)
                return v + frac * (prev_v - v)
            prev_t, prev_v = t, v
        return 0.0

    def bms_taxi_bumps(self, telem_data):
        """Falcon BMS bump effect from the ``BumpIntensity`` telemetry value.

        BMS does not expose nose-wheel weight-on-wheels or ground-speed
        coherent enough for the wheelbase delay scheme used in
        ``ac_update_runway_rumble`` — it only emits a single per-bump
        intensity scalar. We fire both ``runway_impulse`` (stick) and
        ``runway_impulse_delayed`` (shaker) simultaneously so the bump still
        reads as bilateral; the front→rear illusion is a no-op here.
        """
        if not self.runway_rumble_intensity or not self.runway_rumble_enabled:
            effects.dispose("runway_impulse", "runway_impulse_delayed")
            return
        bump = telem_data.get("BumpIntensity")
        if bump and self.anything_has_changed("BumpIntensity", bump):
            intensity = utils.clamp(bump * self.runway_rumble_intensity, 0, 1)
            effects['runway_impulse'].fire_impulse(
                intensity, carrier_hz=28.0, duration_ms=80)
            effects['runway_impulse_delayed'].fire_impulse(
                intensity, carrier_hz=28.0, duration_ms=80)

    def _get_random_direction(self):
        """Get a random direction for weapon effects based on device type."""
        random.seed(time.perf_counter())
        if self.is_pedals():
            return random.choice([90, 270])
        return random.randint(0, 359)

    def _get_effect_direction(self, configured_direction: int) -> int:
        """Get effect direction, either configured or random based on settings."""
        if configured_direction == -1:
            return self._get_random_direction()
        return configured_direction

    def _should_skip_joystick_effect(self) -> bool:
        """Common check for joystick-only effects."""
        return not self.is_joystick()

    def _should_skip_airborne_effect(self, telem_data: dict) -> bool:
        """Common check for effects that should be disabled when on ground."""
        return bool(sum(telem_data.get("WeightOnWheels", [0])))

    def _should_skip_no_airspeed_effect(self, telem_data: dict) -> bool:
        """Common check for effects that require airspeed."""
        return not telem_data.get("TAS", 0)

    def _get_gs_data(self, telem_data: dict) -> tuple:
        """Get G-force data based on simulator type."""
        if self._sim_is("DCS") or self._sim_is("IL2") or self._sim_is('BMS'):
            accs = telem_data.get("ACCs")
            if not accs:
                return None, None, None
            gs = accs[1]
            y_gs = accs[0]
            last_accs = self._last_telem_data.get("ACCs", [0, 0, 0])
            last_y_gs = last_accs[0]
        elif self._sim_is("MSFS") or self._sim_is('XPLANE'):
            gs = telem_data.get("G")
            acc_body = telem_data.get("AccBody")
            if not acc_body:
                return None, None, None
            y_gs = acc_body[2]
            last_acc_body = self._last_telem_data.get("AccBody", [0, 0, 0])
            last_y_gs = last_acc_body[2]
        else:
            return None, None, None
        return gs, y_gs, last_y_gs

    def _is_telemetry_spike(self, y_gs: float, last_y_gs: float, threshold: float = 3.0) -> bool:
        """Check if telemetry shows a spike indicating crash or invalid data."""
        return abs(y_gs - last_y_gs) > threshold

    def _create_weapon_effect(self, effect_name: str, telem_key: str, telem_value,
                             enabled_flag: bool, intensity: float, frequency: int = 10,
                             duration: int = 80, delta_ms: int = 160, shape: int = EFFECT_SINE):
        """Generic method for weapon-related effects (gun, payload, countermeasures)."""
        if not enabled_flag:
            effects[effect_name].stop()
            return

        if self.anything_has_changed(telem_key, telem_value):
            direction = self._get_effect_direction(self.weapon_effect_direction)
            if self.weapon_effect_direction == -1:
                logging.info(f"{effect_name.title()} Effect Direction is randomized: {direction} deg")
            effects[effect_name].periodic(frequency, intensity, direction, effect_type=shape, duration=duration).start(force=True)
        elif not self.anything_has_changed(telem_key, telem_value, delta_ms=delta_ms):
            effects[effect_name].stop()

    def _create_motion_effect(self, telem_data: dict, config: dict):
        """
        Generic method for motion effects (tailhook, fuelboom, wingfold, etc.).

        Args:
            telem_data: Telemetry data dictionary
            config: Configuration dictionary with keys:
                - telem_key: Key in telemetry data
                - change_key: Key for tracking changes
                - effect_names: List of effect names
                - clunk_effects: List of clunk effect names (optional)
                - enabled_attr: Attribute name for enabled flag
                - intensity_attr: Attribute name for intensity
                - frequency: Effect frequency
                - directions: List of directions for effects
                - effect_type: Effect type (optional)
                - delta_ms: Delta time for change detection
                - require_ground: Whether effect requires being on ground
                - phase_offset: Phase offset for secondary effects (optional)
        """
        value = telem_data.get(config['telem_key'])
        if value is None:
            return

        if self._should_skip_joystick_effect():
            return

        # Check ground requirement
        if config.get('require_ground', False):
            on_ground = telem_data.get('SimOnGround', 0)
            if not on_ground:
                effects.dispose(*config['effect_names'])
                return

        enabled = getattr(self, config['enabled_attr'])
        intensity = getattr(self, config['intensity_attr'])

        if not enabled or not intensity:
            effects.dispose(*config['effect_names'])
            return

        delta_ms = config.get('delta_ms', 200)
        if self.anything_has_changed(config['change_key'], value, delta_ms=delta_ms):
            logging.debug(f"{config['telem_key']} Pos: {value}")

            # Create main effects
            for i, effect_name in enumerate(config['effect_names']):
                direction = config['directions'][i] if i < len(config['directions']) else config['directions'][0]
                effect_type = config.get('effect_type', 0)
                phase = config.get('phase_offset', 0) if i > 0 else 0

                effects[effect_name].periodic(
                    config['frequency'],
                    intensity,
                    direction,
                    effect_type,
                    phase=phase
                ).start()
        else:
            # Handle clunk effects when motion stops
            clunk_effects = config.get('clunk_effects', [])
            if clunk_effects and (value == 0 or value == 1):
                if any(effects[name].started for name in config['effect_names']):
                    clunk_intensity = utils.clamp(intensity * 2, 0, 1)

                    for i, clunk_name in enumerate(clunk_effects):
                        direction = config.get('clunk_directions', [180, 0])[i % 2]
                        if config['telem_key'] in ['TailHook', 'FuelBoom']:
                            direction = (1 - value) * 180
                        duration = config.get('clunk_duration', 40)

                        effects[clunk_name].periodic(
                            10,
                            clunk_intensity,
                            direction,
                            effect_type=EFFECT_SQUARE,
                            duration=duration
                        ).start()

            # Stop main effects
            effects.dispose(*config['effect_names'])

    def _create_buffeting_effect(self, telem_data: dict, config: dict):
        """
        Generic method for buffeting effects.

        Args:
            telem_data: Telemetry data dictionary
            config: Configuration dictionary with keys:
                - enabled_attr: Attribute name for enabled flag
                - intensity_attr: Attribute name for intensity
                - frequency_attr: Attribute name for frequency (optional)
                - effect_name: Name of the effect
                - threshold_speed: Minimum speed for effect
                - threshold_value: Minimum deployment value
                - deployment_key: Key for deployment value in telemetry
                - speed_range: Tuple of (low_speed, high_speed) for intensity calculation
                - require_airborne: Whether effect requires being airborne
                - directions: List of directions for multi-axis effects
        """
        enabled = getattr(self, config['enabled_attr'])
        intensity = getattr(self, config['intensity_attr'])

        if not enabled or not intensity:
            effects.dispose(*config['effect_names'])
            return

        tas = telem_data.get("TAS", 0)
        if tas < config.get('threshold_speed', 0):
            effects.dispose(*config['effect_names'])
            return

        deployment = telem_data.get(config['deployment_key'], 0)
        if deployment <= config.get('threshold_value', 0.1):
            effects.dispose(*config['effect_names'])
            return

        if config.get('require_airborne', True) and self._should_skip_airborne_effect(telem_data):
            effects.dispose(*config['effect_names'])
            return

        # Calculate intensity based on speed and deployment
        speed_low, speed_high = config.get('speed_range', (0, 100))
        tas_intensity = utils.scale_clamp(tas, (speed_low, speed_high), (0.0, 1.0))

        if isinstance(deployment, list):
            # Handle special cases like F-14 spoilers
            if config.get('special_calc') and "F-14" in str(telem_data.get("N", "")):
                inner = (deployment[1], deployment[2])
                outer = (deployment[0], deployment[3])
                deployment = (0.85 * sum(inner) + 0.15 * sum(outer)) / 2
            else:
                deployment = sum(deployment) / len(deployment)

        realtime_intensity = intensity * deployment * tas_intensity
        frequency = getattr(self, config.get('frequency_attr', 'frequency'), 13)

        # Create effects
        for i, effect_name in enumerate(config['effect_names']):
            direction = config['directions'][i] if i < len(config['directions']) else config['directions'][0]
            effects[effect_name].periodic(frequency, realtime_intensity, direction, 4).start()

        logging.debug(f"PLAYING {config['effect_name'].upper()} RUMBLE | intensity: {realtime_intensity}")

    def on_event(self, event, *args):
        super().on_event(event, *args)

    @override
    def on_timeout(self):  # override me
        logging.info("Telemetry Timeout, stopping effects")
        # effects.foreach(lambda e: e.stop())
        for key, effect in self.effects.dict.items():
            effect: HapticEffect
            if self.keep_forces_on_pause:
                if effect.effect_type in [
                    EFFECT_SPRING,
                    EFFECT_DAMPER,
                    EFFECT_INERTIA,
                    EFFECT_FRICTION,
                    EFFECT_SPRING_ADJUSTER,
                ]:
                    continue
            effect.stop()

        super().on_timeout()

    @override
    def on_telemetry(self, telem_data: BaseTelemetryData):
        fx, fy = self._get_device_forces()
        self.telem_data.ForceXY = [fx, fy]
        jx, jy = self._get_device_axes()
        self.telem_data.JoyXY = [jx, jy]

        super().on_telemetry(telem_data)