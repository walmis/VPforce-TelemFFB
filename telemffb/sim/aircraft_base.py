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
from telemffb import telem
import telemffb.utils as utils
from typing import List, Dict
# from utils import clamp, HighPassFilter, Derivative, Dispenser

from telemffb.hw.ffb_rhino import EFFECT_TRIANGLE, HapticEffect, FFBReport_SetCondition
from telemffb.hw.ffb_rhino import EFFECT_SPRING,EFFECT_DAMPER, EFFECT_INERTIA, EFFECT_FRICTION, EFFECT_SPRING_ADJUSTER
from telemffb.hw.ffb_rhino import EFFECT_SAWTOOTHUP, EFFECT_SAWTOOTHDOWN
import telemffb.globals as G
from telemffb.globals import master_instance, master_buttons
from telemffb.util.conversions import kt2ms

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


class AircraftBase(object):
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
    speedbrake_speed_thresh :   float = 80 * kt2ms  # speed threshold for speedbrake to start buffeting

    spoiler_motion_intensity: float = 0.0  # peak vibration intensity when spoilers is moving, 0 to disable
    spoiler_buffet_intensity: float = 0.15  # peak buffeting intensity when spoilers deployed,  0 to disable
    spoiler_spd_thresh_low: float = 80 * kt2ms  # speed threshold for spoilers to start buffeting
    spoiler_spd_thresh_hi: float = 140 * kt2ms  # speed threshold for spoilers to stop buffeting

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
    # blade-pass / cylinder-firing crossing of an integrated phase. "synthesis"
    # is the legacy continuous-sine path. Physics mode only takes effect on
    # the shaker backend; the Rhino backend degrades to a periodic at the same
    # carrier frequency regardless.
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
        self._name = name
        self._changes = {}
        self._change_counter = {}
        self._telem_data = {}
        self._last_telem_data = {}
        self._ipc_telem = {}
        self.adv_g_settings_dict: dict = {}
        self.adv_spr_settings_dict: dict = {}
        self.active_deadzone_pct: float = 0.0
        self.deadzone_active = False

        self.hydraulic_factor = 0.000
        #clear any existing effects
        effects.clear()

        self.spring_x = FFBReport_SetCondition(parameterBlockOffset=0)
        self.spring_y = FFBReport_SetCondition(parameterBlockOffset=1)
        self.spring_adjuster_x = FFBReport_SetCondition(parameterBlockOffset=0)
        self.spring_adjuster_y = FFBReport_SetCondition(parameterBlockOffset=1)
        self.spring_adjuster = effects['spring_adjuster'].spring_adjuster()
        self.offset_adjuster_x = FFBReport_SetCondition(parameterBlockOffset=0)
        self.offset_adjuster_y = FFBReport_SetCondition(parameterBlockOffset=1)
        self.offset_adjuster = effects['offset_adjuster'].spring_adjuster()

        self.friction_effect_overridden: bool = False

        self.SpringModeEnum = G.settings_mgr.SpringModeEnum
        self.GEffectModeEnum = G.settings_mgr.GEffectModeEnum

        self.spring_mode = self.SpringModeEnum.NONE.name
        self.gforce_effect_mode = self.GEffectModeEnum.DISABLED.name

    def spring_mode_is(self, mode):
        return mode.name == self.spring_mode

    def gforce_effect_mode_is(self, mode):
        return mode.name == self.gforce_effect_mode

    def step_value_over_time(self, key, value, timeframe_ms, dst_val, floatpoint=False):
        '''
        This function creates an entry in the  stepper dictionary which can be used to track the progress of driving a
        value from "a to b" over a period of time across multiple passes through the effects loop.
        '''

        if value == dst_val:
            return value

        current_time_ms = time.perf_counter() * 1000  # Start time for the current step
        # current_time_end = current_time_start  # End time for the current step (initially the same as start time)

        # add a new key to the dictionary if one does not exist and initialize the tracking variables
        if key not in self.stepper_dict:
            self.stepper_dict[key] = {
                'value': value,
                'dst_val': dst_val,
                'start_time': current_time_ms,
                'end_time': current_time_ms + timeframe_ms,
                'timeframe': timeframe_ms,
                'last_iteration_ms': current_time_ms
            }
            return value
        else:
            # if it already exists, but the dst_value has changed, the condition probably changed before the timer expired, so reset the key to new condition
            if self.stepper_dict[key]['dst_val'] != dst_val:
                self.stepper_dict[key] = {
                    'value': value,
                    'dst_val': dst_val,
                    'start_time': current_time_ms,
                    'end_time': current_time_ms + timeframe_ms,
                    'timeframe': timeframe_ms,
                    'last_iteration_ms': current_time_ms
                }
                return value

        data = self.stepper_dict[key]

        iteration_ms = current_time_ms - data['last_iteration_ms']  # calculate time since last iteration

        data['last_iteration_ms'] = current_time_ms  # reset iteration timestamp to current timestamp

        delta_to_go = data['dst_val'] - data['value']  # calculate distance left to move the value
        time_to_go = data['end_time'] - current_time_ms  # calculate time left to move the value to destination

        step_size = (iteration_ms / time_to_go) * delta_to_go  # calculate step size to reach target at time

        if ((data['dst_val'] - data['value']) * (data['dst_val'] - (data['value'] + step_size))) <= 0:
            # We crossed the target
            data['value'] = data['dst_val']
            del self.stepper_dict[key]
            return data['dst_val']

        if data['value'] == data['dst_val']:  # if we have reached the dst value, delete the key and return the value
            del self.stepper_dict[key]
            return data['value']

        elapsed_time_ms = (current_time_ms - data['start_time'])

        if elapsed_time_ms >= timeframe_ms:  # if the elapsed time is greater than the given timeframe, return the destination value
            data['value'] = data['dst_val']
            del self.stepper_dict[key]
            return data['dst_val']

        val = value + step_size

        if not floatpoint:  # if floatpoint is not specified, return a rounded integer value
            val = round(val)
        data['value'] = val
        # print(f"value out = {data['value']}")
        return data['value']

    def apply_settings(self, settings_dict):
        """Apply settings from a configuration dictionary to the aircraft instance.

        Args:
            settings_dict (dict): Dictionary containing configuration key-value pairs.
                                Keys should match aircraft attribute names.

        Logs warnings for unknown parameters and info for each applied setting.
        """
        for k, v in settings_dict.items():
            if k in ["type"]: continue
            if k.endswith("_group"): continue
            if getattr(self, k, None) is None and k != 'vpconf' and 'dummy' not in k and 'command_runner' not in k:
                # logging.info(f"WARNING: Unknown parameter {k} in config")  # This log is no longer relevant since we moved away from ini files
                continue
            logging.info(f"set {k} = {v}")
            setattr(self, k, v)

    def has_changed(self, item: str, delta_ms=0, data=None) -> bool:
        """Check if a telemetry data item has changed since last call.

        Args:
            item (str): Name of the telemetry data item to check
            delta_ms (int, optional): Time window in milliseconds to consider as "recently changed". Defaults to 0.
            data (dict, optional): Telemetry data dictionary to use. Defaults to self._telem_data.

        Returns:
            bool: True if the item changed, False otherwise.
                 If the item changed, returns a tuple (prev_val, new_val) instead.
        """
        if data == None:
            data = self._telem_data

        prev_val, tm = self._changes.get(item, (None, 0))
        new_val = data.get(item)

        # round floating point numbers
        if type(new_val) == float:
            new_val = round(new_val, 3)

        if prev_val != new_val:
            self._changes[item] = (new_val, time.perf_counter())

        if prev_val != new_val and prev_val is not None and new_val is not None:
            return (prev_val, new_val)

        if time.perf_counter() - tm < delta_ms / 1000.0:
            return True

        return False

    def flag_error(self, message):
        """Flag an error message for display in the UI.

        Args:
            message (str): Error message to display
        """
        dev = self.telem_data.get('FFBType', 'joystick').capitalize()
        self.telem_data['error'] = message
        if not master_instance:
            self._ipc_telem['error'] = f"{dev}: {message}"

    def is_joystick(self):
        """Check if the current FFB device is a joystick.

        Returns:
            bool: True if device is a joystick, False otherwise
        """
        return self._telem_data.get("FFBType", "joystick") == "joystick"
    
    def is_pedals(self):
        """Check if the current FFB device is pedals.

        Returns:
            bool: True if device is pedals, False otherwise
        """
        return self._telem_data.get("FFBType") == "pedals"

    def is_collective(self):
        """Check if the current FFB device is a collective.

        Returns:
            bool: True if device is a collective, False otherwise
        """
        return self._telem_data.get("FFBType") == "collective"

    def is_trimwheel(self):
        """Check if the current FFB device is a trim wheel.

        Returns:
            bool: True if device is a trim wheel, False otherwise
        """
        return self._telem_data.get("FFBType") == "trimwheel"

    def is_shaker(self):
        """Check if the current FFB device is a bass shaker.

        Returns:
            bool: True if device is a bass shaker, False otherwise
        """
        return self._telem_data.get("FFBType") == "shaker"


    def anything_has_changed(self, item: str, value, delta_ms=0):
        """track if any parameter, given as key "item" has changed between two consecutive calls of the function
        delta_ms can be used to smooth the effects of telemetry which does not update regularly but is still "moving"
        a positive delta_ms value will allow the data to remain unchanged for that period of time before returning false"""

        prev_val, tm, changed_yet = self._changes.get(item, (None, 0, 0))
        new_val = value
        new_tm = time.perf_counter()
        # round floating point numbers
        if type(new_val) == float:
            new_val = round(new_val, 3)

        # make sure we do not return true until the key has changed at least once (after init)
        if prev_val == None and not changed_yet:
            self._changes[item] = (new_val, tm, 0)
            prev_val = new_val

        # logging.debug(f"Prev: {prev_val}, New: {new_val}, TM: {tm}")

        if prev_val != new_val:
            self._changes[item] = (new_val, new_tm, 1)

        if prev_val != new_val and prev_val is not None and new_val is not None:
            return (prev_val, new_val, new_tm - tm)

        if time.perf_counter() - tm < delta_ms / 1000.0:
            return True

        return False
    
    def _sim_is_msfs(self, *unused):
        """Check if the current simulator is Microsoft Flight Simulator.

        Returns:
            bool: True if MSFS, False otherwise
        """
        return self._telem_data.get("src") == "MSFS"

    def _sim_is_xplane(self):
        """Check if the current simulator is X-Plane.

        Returns:
            bool: True if X-Plane, False otherwise
        """
        return self._telem_data.get('src') == "XPLANE"

    def _sim_is_dcs(self, *unused):
        """Check if the current simulator is DCS World.

        Returns:
            bool: True if DCS, False otherwise
        """
        return self._telem_data.get("src") == "DCS"

    def _sim_is_bms(self, *unused):
        """
        Check if the current simulator is BMS.

        Returns:
            bool: True if DCS, False otherwise
        """
        return self._telem_data.get("src") == "BMS"

    def _sim_is_il2(self, *unused):
        """Check if the current simulator is IL2 Sturmovik..

                Returns:
                    bool: True if IL2, False otherwise
                """
        return self._telem_data.get("src") == "IL2"

    def _sim_is(self, sim, *unused):
        """Check if the current simulator matches the specified name.

        Args:
            sim (str): Simulator name to check against

        Returns:
            bool: True if matches, False otherwise
        """
        return self._telem_data.get('src') == sim

    ########################################
    ######                            ######
    ######  Generic Aircraft Effects  ######
    ######                            ######
    ########################################

    def ac_update_touchdown_effect(self, telem_data):
        """Generates a g-based force upon landing or as a result of large bumps.

        On the WoW rising edge we additionally fire a one-shot impulse
        whose magnitude scales with the smoothed vertical speed at the
        moment of touchdown — gentle landing barely felt, carrier slam at
        full magnitude. This is independent of the steady G-force-driven
        constant the legacy path applies while rolling.
        """

        if self.is_collective() or self.is_pedals():
            return
        if self._sim_is("DCS") or self._sim_is("IL2") or self._sim_is('BMS'):
            gs = round(telem_data.get("ACCs")[1] - 1, 2)  # subtract nominal G to align with zero based data from MSFS
        elif self._sim_is("MSFS") or self._sim_is("XPLANE"):
            gs = round(telem_data.get("AccBody")[1], 2)
        else:
            return

        if not self.touchdown_effect_enabled:
            effects.dispose("touchdown")
            return
        on_ground = bool(telem_data.get("SimOnGround", 0))

        # VS-driven one-shot on the WoW rising edge. Smoothed VS captures the
        # actual descent rate at the moment of contact, not the post-touchdown
        # bounce which the LPF would otherwise contaminate the read with.
        if self.touchdown_vs_enabled:
            vs_raw = telem_data.get("VerticalSpeed", 0.0) or 0.0
            try:
                vs_raw = float(vs_raw)
            except (TypeError, ValueError):
                vs_raw = 0.0
            vs_smoothed = LPFs.get("touchdown_vs", 15).update(vs_raw)
            # anything_has_changed returns True on a value transition; for a
            # bool we get the rising AND the falling edge. We only fire on
            # the air→ground edge, so guard with on_ground.
            wow_changed = self.anything_has_changed("touchdown_wow", on_ground)
            if wow_changed and on_ground:
                amp = utils.scale_clamp(
                    abs(vs_smoothed),
                    (self.touchdown_vs_gentle, self.touchdown_vs_hard),
                    (self.touchdown_vs_min_amp, self.touchdown_vs_max_amp))
                logging.info(f"Touchdown impulse: VS={vs_smoothed:.2f} m/s -> amp={amp:.2f}")
                effects['touchdown_vs'].fire_impulse(amp)

        if not on_ground:
            effects.dispose("touchdown")
            return
        force = round(utils.scale_clamp(gs, (0, self.touchdown_effect_max_gs), (0,self.touchdown_effect_max_force)), 2)

        logging.debug(f"Touchdown Effect: Realtime Gs: {gs}, Force:{force}")
        effects['touchdown'].constant(force, 180).start()

    def bms_taxi_bumps(self, telem_data):
        """Generates a bump effect in response to bumpIntensity telemetry for Falcon BMS simulator"""
        if not self.runway_rumble_intensity or not self.runway_rumble_enabled:
            effects.dispose("runway_bump0", "runway_bump1")
            return
        bump = telem_data.get("BumpIntensity")
        if bump and self.anything_has_changed("BumpIntensity", bump):
            intensity = utils.clamp(bump * self.runway_rumble_intensity, 0, 1)
            effects['runway_bump0'].periodic(15, intensity * .75, direction=0, effect_type=EFFECT_SQUARE, duration=80).start()
            effects['runway_bump1'].periodic(15, intensity, direction=0, effect_type=EFFECT_SQUARE, phase=180, duration=160).start()


    def ac_update_runway_rumble(self, telem_data):
        """Add wheel based rumble effects for immersion
        Generates bumps/etc on touchdown, rolling, field landing etc
        """
        if self._sim_is_bms():
            # Fake it since BMS does not have weight on wheels - only has 'bumps' telemetry
            self.bms_taxi_bumps(telem_data)
            return

        if self.is_collective(): return
        if not self.runway_rumble_intensity or not self.runway_rumble_enabled:
            effects.dispose("runway0", "runway1")
            return

        WoW = telem_data.get("WeightOnWheels", (0, 0, 0))  # nose, left, right - wheels
        # get high pass filters for wheel shock displacement data and update with latest data
        hp_f_cutoff_hz = 3
        v1 = HPFs.get("center_wheel", hp_f_cutoff_hz).update((WoW[0])) * self.runway_rumble_intensity
        v2 = HPFs.get("side_wheels", hp_f_cutoff_hz).update(WoW[1] - WoW[2]) * self.runway_rumble_intensity

        v1 = utils.clamp_minmax(v1, 0.5)
        v2 = utils.clamp_minmax(v2, 0.5)

        # modulate constant effects for X and Y axis
        # connect Y axis to nosewheel, X axis to the side wheels
        tot_weight = sum(WoW)

        #if telem_data.get("T", 0) > 2:  # wait a bit for data to settle
        if tot_weight:
            logging.debug(f"Runway Rumble : v1 = {v1}. v2 = {v2}")
            effects["runway0"].constant(v1, utils.RandomDirectionModulator).start()
            effects["runway1"].constant(v2, utils.RandomDirectionModulator).start()
        else:
            effects.dispose("runway0", "runway1")

    def _ac_run_new_gforce_effect(self, telem_data):
        """Apply new G-force effects based on aircraft acceleration.

        Generates force feedback effects that vary with G-forces experienced by the aircraft.
        The effect strength is modulated by stick deflection and can handle both positive
        and negative G-forces if configured.

        Args:
            telem_data (dict): Telemetry data containing acceleration information
        """
        if self._should_skip_joystick_effect() or not self.gforce_effect_mode_is(self.GEffectModeEnum.NEW) or self.gforce_effect_mode_is(self.GEffectModeEnum.DISABLED):
            effects.dispose("new_gforce")
            return
        if self._should_skip_airborne_effect(telem_data):
            effects.dispose("new_gforce")
            return
        if self._should_skip_no_airspeed_effect(telem_data):
            effects.dispose("new_gforce")
            return

        gmin = self.new_gforce_min_gs
        gmin_neg = self.new_gforce_min_gs_neg
        gmax = self.new_gforce_max_gs
        gmax_neg = self.new_gforce_max_gs_neg

        gs, y_gs, last_y_gs = self._get_gs_data(telem_data)
        if gs is None:
            return

        if self._is_telemetry_spike(y_gs, last_y_gs):
            effects.dispose("new_gforce")
            return

        logging.debug(f"GS={gs}, AVG_Z_GS={gs}")

        if gmin_neg < gs < gmin:
            effects["new_gforce"].stop()
            return

        input_data = HapticEffect.device.get_input()
        x, y = input_data.axisXY()
        _, spring_y_center = input_data.CP_XY()
        if spring_y_center is None:
            spring_y_center = 0
        derivative_hz = 5  # derivative lpf filter -3db Hz
        derivative_k = 0.1  # derivative gain value, or damping ratio

        dGs = getattr(self, "_dGs", None)
        if not dGs: dGs = self._dGs = utils.Derivative(derivative_hz)
        dGs.lpf.cutoff_freq_hz = derivative_hz

        if gs > 1 and y > (spring_y_center + self.new_gforce_effect_center_deadzone):
            direction = 180
            g_factor = utils.scale_clamp(gs, (gmin, gmax), (0,1))

            g_deriv = - dGs.update(g_factor) * derivative_k
            g_factor += g_deriv
            y_maxpoint = spring_y_center + (1 - spring_y_center) * self.new_gforce_effect_deflection_factor
            # utils.dbprint("green", f"y: {y}, syc:{spring_y_center}, y_max: {y_maxpoint}")
            deflection_factor = abs(utils.scale(y, (spring_y_center, y_maxpoint), (0, 1)))
        elif gs < 1 and y < (spring_y_center - self.new_gforce_effect_center_deadzone) and self.new_gforce_enable_neg_gs:
            direction = 0
            g_factor = utils.scale_clamp(gs, (gmin_neg, gmax_neg), (0,1))

            g_deriv = - dGs.update(g_factor) * derivative_k
            g_factor += g_deriv
            y_maxpoint = abs(spring_y_center + (-1 - spring_y_center) * self.new_gforce_effect_deflection_factor_neg)
            # utils.dbprint("red", f"y_pos: {y}, spr_cent:{spring_y_center}, y_max: {y_maxpoint}")
            deflection_factor = abs(utils.scale(y, (spring_y_center, y_maxpoint), (0, -1)))
        else:
            effects["new_gforce"].stop()
            return

        telem_data['g_factor_raw'] = g_factor
        telem_data['g_deflection'] = deflection_factor
        # utils.dbprint("blue", f"g_deflection_factor: {deflection_factor}", "joystick")
        telem_data['g_y'] = y

        g_factor = g_factor * deflection_factor

        telem_data['g_factor'] = g_factor
        effects["new_gforce"].constant(g_factor, direction).start()
        logging.debug(f"G's = {gs} | gfactor = {g_factor}")

    def ac_update_gforce_effect(self, telem_data, adv_spr=False):
        if self.gforce_effect_mode_is(self.GEffectModeEnum.DISABLED):
            effects.dispose('gforce', 'new_gforce')
            return
        if self.gforce_effect_mode_is(self.GEffectModeEnum.NEW):
            # if "New" Gforce effect is enabled, call it instead and ensure the effect is disposed
            effects.dispose("gforce")
            self._ac_run_new_gforce_effect(telem_data)
            return
        else:
            effects.dispose("new_gforce")

        if self._should_skip_joystick_effect():
            effects.dispose("gforce")
            return

        if self.gforce_effect_mode_is(self.GEffectModeEnum.ADVANCED):
            # Verify the device firmware meets the minimum version required to execute this portion of the effect
            # Flag error and abort if not met
            supported = utils.check_min_firmware_version(G.device_firmware_version, "v1.0.18")
            if not supported:
                self.flag_error('The Advanced/Custom Curve G-Force effect requires firmware v1.0.18 or higher.\n'
                                f'The device is currently running version {G.device_firmware_version}\n'
                                f'Please update your device firmware!')
                return

        if self._should_skip_airborne_effect(telem_data):
            effects.dispose("gforce")
            return
        if self._should_skip_no_airspeed_effect(telem_data):
            effects.dispose("gforce")
            return

        gs, y_gs, last_y_gs = self._get_gs_data(telem_data)
        if gs is None:
            return

        if self._is_telemetry_spike(y_gs, last_y_gs):
            effects.dispose("gforce")
            return

        logging.debug(f"GS={gs}, AVG_Z_GS={gs}")

        if self.gforce_effect_mode_is(self.GEffectModeEnum.LEGACY):
            gmin = self.gforce_min_gs
            gmax = self.gforce_max_gs
            direction = 180
            if gs < gmin:
                effects["gforce"].stop()
                return
            g_factor = round(utils.non_linear_scaling(gs, gmin, gmax, curvature=self.gforce_effect_curvature), 4)

            derivative_hz = 5  # derivative lpf filter -3db Hz
            derivative_k = 0.1  # derivative gain value, or damping ratio

            dGs = getattr(self, "_dGs", None)
            if not dGs: 
                dGs = self._dGs = utils.Derivative(derivative_hz)
                
            dGs.lpf.cutoff_freq_hz = derivative_hz

            g_deriv = - dGs.update(g_factor) * derivative_k

            g_factor += g_deriv

            g_factor = utils.clamp(g_factor, 0.0, 1.0)

            effects["gforce"].constant(g_factor, direction).start()

            logging.debug(f"G's = {gs} | gfactor = {g_factor}")

        elif self.gforce_effect_mode_is(self.GEffectModeEnum.ADVANCED):
            if self.gforce_effect_adv_curve == 'none':
                self.flag_error('Please Configure the Advanced G-Force Effect Settings')
                effects.dispose('adv_gforce_constant')
                return
            if self.adv_g_settings_dict == {}:
                self.adv_g_settings_dict = utils.json.loads(self.gforce_effect_adv_curve)

            gains = utils.get_gain_from_gs(self.gforce_effect_adv_curve, abs(gs))

            mode = self.adv_g_settings_dict.get('mode', 'constant')

            if gs >= 0:
                g_factor = gains.get('pos')
                direction = 180
            else:
                if self.adv_g_settings_dict.get('enable_neg'):
                    g_factor = -gains.get('neg')
                    direction = 0
                else:
                    effects.dispose('gforce', 'gforce_spr')
                    return

            if not g_factor:
                effects.dispose('gforce', 'gforce_spr')
                return
            if mode == 'constant':
                g_factor = utils.clamp(g_factor, 0.0, 1.0)
                effects["gforce"].constant(g_factor, direction).start()

            elif mode == 'offset':
                adjuster_cpOy = int(-g_factor*4096)

                if adv_spr:
                    # If being called by advanced spring effect, don't apply adjuster offset here, return offset value and let the advanced spring adjuster effect do it
                    return adjuster_cpOy

                self.offset_adjuster.name = 'gforce_spr'
                self.offset_adjuster_y.set_offset(adjuster_cpOy)
                self.offset_adjuster_y.set_saturation(4096)
                self.offset_adjuster_x.set_saturation(4096)
                self.offset_adjuster.setCondition(self.offset_adjuster_y)
                self.offset_adjuster.setCondition(self.offset_adjuster_x)
                self.offset_adjuster.start()

        else:
            effects.dispose("gforce")
            return


    def ac_update_aoa_reduction_force_effect(self, telem_data):
        if not self.aoa_reduction_effect_enabled:
            return
        if self._should_skip_joystick_effect():
            return
        if self._should_skip_airborne_effect(telem_data):
            effects.dispose("crit_aoa")
            return
        if self._should_skip_no_airspeed_effect(telem_data):
            effects.dispose("crit_aoa")
            return
        start_aoa = self.critical_aoa_start
        end_aoa = self.critical_aoa_max
        aoa = telem_data.get("AoA", 0)
        tas = telem_data.get("TAS", 0)
        avg_aoa = self.smoother.get_average("crit_aoa", aoa, sample_size=8)
        if avg_aoa >= start_aoa and tas > 10:
            force_factor = round(utils.non_linear_scaling(avg_aoa, start_aoa, end_aoa, curvature=1.5), 4)
            force_factor = self.aoa_reduction_max_force * force_factor
            force_factor = utils.clamp(force_factor, 0.0, 1.0)
            logging.debug(f"AoA Reduction Effect:  AoA= {aoa} avg_AoA={avg_aoa}, force={force_factor}, max allowed force={self.aoa_reduction_max_force}")
            effects["crit_aoa"].constant(force_factor, 180).start()
        else:
            effects.dispose("crit_aoa")
        return

    def ac_update_decel_effect(self, telem_data):
        if not self.deceleration_effect_enable or not self.is_joystick():
            effects.dispose("decel")
            return

        wow = sum(telem_data.get("WeightOnWheels"), 0)
        if not wow and self.decel_airborne_disable:
            # When off ground, dispose effect and return
            effects.dispose('decel')
            return

        if self._sim_is("DCS") or self._sim_is("IL2") or self._sim_is("BMS"):
            if self.decel_airborne_disable:
                # We are on the ground, calculate using G vectors
                y_gs = telem_data.get("ACCs", 0)[0]
                last_y_gs = self._last_telem_data.get("ACCs", [0, 0, 0])[0]
            else:
                # we are in the air, calculate G vector from rate of change of velocity since DCS Y g vector is world orientation

                # if telem_data.get('speedbrakes_value', 0) <= 0.1:
                #     # don't play decel effect while in the air unless the airbrake is deployed
                #     effects.dispose("decel")
                #     return

                dt = perftracker.get_time_delta('decel')
                speed = telem_data.get('TAS')

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

                self.telem_data['decel_g'] = accel_g

                y_gs = accel_g
                last_y_gs = self.last_y_gs
                self.last_y_gs = y_gs

        elif self._sim_is("MSFS"):
            y_gs = telem_data.get("AccBody")[2]
            last_y_gs = self._last_telem_data.get("AccBody", [0, 0, 0])[2]

        elif self._sim_is_xplane():
            y_gs = -telem_data.get("Gaxil")
            last_y_gs = self._last_telem_data.get("Gaxil", 0)
        delta_y = abs(y_gs) - abs(last_y_gs)

        if not self.anything_has_changed("decel", y_gs):
            return

        if abs(delta_y) > 3:  # If the per-frame rate of change is greater than 3 Gs, we have likely crashed and telemetry is violently spiking.. do not play effect:
            return

        if not telem_data.get("TAS", 0):
            effects.dispose("decel")
            return
        avg_y_gs = self.smoother.get_average("y_gs", y_gs, sample_size=8)

        self.telem_data['decel_g_smooth'] = avg_y_gs

        max_gs = self.deceleration_max_force

        dir = 180 if not self.decel_invert_force else 0

        if (avg_y_gs < -0.03 < 500):  # Don't play effect for very small, or very large (crash) force values, or no weight on wheels
            if abs(avg_y_gs) > max_gs:
                avg_y_gs = -max_gs

            avg_y_gs = utils.clamp(abs(avg_y_gs) * self.decel_scale_factor, 0, 1)
            if self._sim_is_dcs() and not wow:
                sb = telem_data.get('speedbrakes_value')
                avg_y_gs = avg_y_gs * sb
            logging.debug(f"y_gs = {y_gs} avg_y_gs = {avg_y_gs}")
            effects["decel"].constant(abs(avg_y_gs), direction=dir).start()
        else:
            effects.dispose("decel")

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
        max_airflow_speed = 75*knots  # speed at which airflow_factor is 1.0
        airflow_factor = utils.scale_clamp(speed, (0, max_airflow_speed), (0, 1.0))
        buffeting_factor = utils.scale_clamp(aoa, (local_buffet_aoa, local_stall_aoa), (0.0, 1.0))
        # todo calc frequency
        return (self.aoa_buffet_freq, airflow_factor * buffeting_factor * self.buffeting_intensity)

    def ac_update_buffeting(self, telem_data: dict):
        if not self.buffeting_intensity or not self.aoa_buffeting_enabled:
            return
        
        aoa = telem_data.get("AoA", 0)
        tas = telem_data.get("TAS", 0)

        max_airflow_speed = 75*knots  # speed at which airflow_factor is 1.0

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
            effects.dispose("buffeting")
            return
        if local_buffet_aoa == 0 or local_stall_aoa == 0:
            return
        if max(telem_data.get('WeightOnWheels', 0)):
            effects.dispose("buffeting")
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
        effects["buffeting"].periodic(freq, mag, utils.RandomDirectionModulator).start()
        # effects["buffeting2"].periodic(freq, mag, 45, phase=120).start()

        telem_data["_buffeting"] = mag  # save debug value

    def ac_update_drag_buffet(self, telem_data: dict, type: str):
        drag_buffet_threshold = 100  # indicated TAS via telemetry
        tas = telem_data.get("TAS", 0)
        if tas < drag_buffet_threshold:
            return 0

    def ac_update_cm_weapons(self, telem):
        payload = telem.get("PayloadInfo")
        gun = telem.get("Gun")
        flares = telem.get("Flares")
        chaff = telem.get("Chaff")

        # Use helper method for weapon effects
        self._create_weapon_effect("payload_rel", "PayloadInfo", payload,
                                 self.weapon_release_effect_enabled, self.weapon_release_intensity, shape=EFFECT_SQUARE)

        self._create_weapon_effect("gunfire", "Gun", gun,
                                 self.gunfire_effect_enabled, self.gun_vibration_intensity, shape=EFFECT_SAWTOOTHUP)

        # Countermeasures effect (combined flares and chaff)
        cm_changed = (self.anything_has_changed("Flares", flares) or
                     self.anything_has_changed("Chaff", chaff))
        if cm_changed and self.countermeasure_effect_enabled:
            direction = self._get_effect_direction(self.weapon_effect_direction)
            if self.weapon_effect_direction == -1:
                logging.info(f"CM Effect Direction is randomized: {direction} deg")
            effects["cm"].periodic(50, self.cm_vibration_intensity, direction, duration=80).start(force=True)
        elif not (self.anything_has_changed("Flares", flares, delta_ms=160) or
                 self.anything_has_changed("Chaff", chaff, delta_ms=160)) or not self.countermeasure_effect_enabled:
            effects["cm"].stop()

    def ac_update_flaps(self, telem_data):
        flapspos = telem_data.get("Flaps")
        if flapspos is None:
            flapspos = telem_data.get("flaps_value")
        if flapspos is None:
            return
        
        if isinstance(flapspos, list):
            flapspos = max(flapspos)
        if self.anything_has_changed("Flaps", flapspos, delta_ms=100) and self.flaps_motion_intensity > 0 and self.flaps_motion_effect_enabled:
            logging.debug(f"Flaps Pos: {flapspos}")
            direction = 90 if self.is_pedals() else 0
            effects["flapsmovement"].periodic(180, self.flaps_motion_intensity, direction, 3).start()
        else:
            effects["flapsmovement"].stop(destroy_after=5000)

    def ac_update_canopy(self, telem_data):
        canopypos = telem_data.get("Canopy")
        if canopypos is None:
            canopypos = telem_data.get("canopy_value")
        if canopypos is None:
            return
        # canopypos = self._telem_data.get("canopy_value", 0)
        if self.anything_has_changed("Canopy", canopypos, delta_ms=100) and self.canopy_motion_intensity > 0 and self.canopy_motion_effect_enabled:
            logging.debug(f"Canopy Pos: {canopypos}")
            direction = 90 if self.is_pedals() else 0
            effects["canopymovement"].periodic(120, self.canopy_motion_intensity, direction, 3).start()
        else:
            # play short bump when canopy fully closes, play only once before movement effect is stopped
            if canopypos == 0 and effects['canopymovement'].started:
                effects['canopyclunk'].periodic(10, utils.clamp((self.canopy_motion_intensity * 2), 0, 1), 180, effect_type=EFFECT_SQUARE,duration=40).start()
            # stop movement effect
            effects["canopymovement"].stop(destroy_after=5000)

    def ac_update_landing_gear(self, telem_data):
        gearpos = telem_data.get("gear_value", telem_data.get("GearPos", None))
        if isinstance(gearpos, list):
            gearpos = max(gearpos)

        if self._sim_is_msfs() or self._sim_is_xplane():
            retracts = telem_data.get("RetractableGear", 0)
            if isinstance(retracts, list):
                retracts = max(retracts)
            if (self.gear_motion_intensity > 0) and (retracts):
                gearpos = max(telem_data.get("Gear", 0))
        
        if gearpos is None:
            return
        
        airspd = telem_data.get("IAS", telem_data.get("TAS", None)) # IAS or TAS ?
        if airspd is None:
            return
        
        if self._sim_is_xplane():
            self.gear_buffet_speed_low = 0.9 * self.telem_data.get("Vle", 10000) #set stupid high in case of telemetry failure
            self.gear_buffet_speed_high = self.gear_buffet_speed_low * 1.3

        rumble_freq = self.gear_buffet_freq

        # Gear Motion Effect
        if self.anything_has_changed("gear_value", gearpos, 50) and self.gear_motion_intensity > 0 and self.gear_motion_effect_enabled:
            logging.debug(f"Landing Gear Pos: {gearpos}")
            effects["gearmovement"].periodic(150, self.gear_motion_intensity, 0, 3).start()
            effects["gearmovement2"].periodic(150, self.gear_motion_intensity, 90, 3, phase=120).start()
            if (gearpos == 0 or gearpos == 1) and (self.is_joystick() or self.is_shaker()):
                # Play short, sharp bump when gear fully extended/retracted
                dir = 0 if gearpos == 0 else 180  # change direction of effect depending on if gear are closed or extended
                effects['gearclunk'].periodic(10, utils.clamp((self.gear_motion_intensity * 3), 0, 1), dir, effect_type=EFFECT_SQUARE,duration=40).start()
        else:
            effects.dispose("gearmovement", "gearmovement2")

        # Gear Buffeting Effect
        if (airspd > self.gear_buffet_speed_low and gearpos > .1) and self.gear_buffet_intensity > 0 and self.gear_buffet_effect_enabled:
            # calculate insensity based on deployment percentage
            # intensity will go from 0 to %100 configured between spd_thresh_low and spd_thresh_high
            realtime_intensity = utils.scale_clamp(airspd, (self.gear_buffet_speed_low, self.gear_buffet_speed_high),(0, self.gear_buffet_intensity)) * gearpos
            effects["gearbuffet"].periodic(rumble_freq, realtime_intensity, 0, 4).start()
            effects["gearbuffet2"].periodic(rumble_freq, realtime_intensity, 90, 4).start()
            logging.debug(f"PLAYING GEAR RUMBLE intensity:{realtime_intensity}")
        else:
            effects.dispose("gearbuffet", "gearbuffet2")

    def ac_update_speed_brakes(self, telem_data):
        if self._telem_data.get("AircraftClass", "GenericAircraft") == 'Helicopter':
            return

        spd_thresh = self.speedbrake_speed_thresh
        spdbrk = telem_data.get("SpeedbrakePos")
        if spdbrk is None:
            spdbrk = telem_data.get("speedbrakes_value", None)

        if spdbrk is None:
            return

        airspd = telem_data.get("IAS", telem_data.get("TAS", None))
        if airspd is None:
            return
        # Speed brake motion effect
        if self.anything_has_changed("speedbrakes_value", spdbrk, 50) and self.speedbrake_motion_intensity > 0 and self.speedbrake_motion_effect_enabled:
            logging.debug(f"Speedbrake Pos: {spdbrk}")
            direction = 90 if self.is_pedals() else 0
            effects["speedbrakemovement"].periodic(180, self.speedbrake_motion_intensity, direction, 3).start()
        else:
            effects.dispose("speedbrakemovement")

        # Speed brake buffeting effect using helper
        config = {
            'enabled_attr': 'speedbrake_buffet_effect_enabled',
            'intensity_attr': 'speedbrake_buffet_intensity',
            'effect_names': ['speedbrakebuffet'],
            'effect_name': 'speedbrake',
            'threshold_speed': spd_thresh,
            'threshold_value': 0.1,
            'deployment_key': 'speedbrakes_value',
            'directions': [utils.RandomDirectionModulator],
            'require_airborne': False
        }
        # Create a temporary telem_data with speedbrake value
        temp_telem = dict(self._telem_data)
        temp_telem['speedbrakes_value'] = spdbrk
        temp_telem['TAS'] = airspd
        self._create_buffeting_effect(temp_telem, config)

    def ac_update_spoilers(self, telem_data):
        if self._telem_data.get("AircraftClass", "GenericAircraft") == 'Helicopter':
            return

        spd_thresh_low = self.spoiler_spd_thresh_low
        spd_thresh_hi  = self.spoiler_spd_thresh_hi

        airspd = telem_data.get("IAS", telem_data.get("TAS", None))
        if airspd is None:
            return

        tas_intensity = utils.clamp_minmax(utils.scale(airspd, (spd_thresh_low, spd_thresh_hi), (0.0, 1.0)), 1.0)
        spoiler = telem_data.get("Spoilers")

        if spoiler == 0 or spoiler == None:
            effects.dispose("spoilermovement", "spoilermovement2")
            return
        # average all spoiler values together
        if isinstance(spoiler, list):
            spoiler = sum(spoiler) / len(spoiler)

        if self.spoiler_motion_intensity > 0 and self.spoiler_motion_intensity > 0 and self.spoiler_motion_effect_enabled:
            if self.anything_has_changed("Spoilers", spoiler, delta_ms=50):
                logging.debug(f"Spoilers Pos: {spoiler}")
                effects["spoilermovement"].periodic(118, self.spoiler_motion_intensity, 0, 4).start()
                effects["spoilermovement2"].periodic(118, self.spoiler_motion_intensity, 90, 4).start()
            else:
                logging.debug("Destroying Spoiler Effects")
                for effect in ["spoilermovement", "spoilermovement2"]:
                    effects[effect].stop(1000)

        if airspd > spd_thresh_low and spoiler > .1 and self.spoiler_buffet_intensity > 0 and self.spoiler_buffet_effect_enabled:
            # calculate insensity based on deployment percentage
            realtime_intensity = self.spoiler_buffet_intensity * spoiler * tas_intensity
            logging.debug(f"PLAYING SPOILER RUMBLE | intensity: {realtime_intensity}, d-factor: {spoiler}, s-factor: {tas_intensity}")
            effects["spoilerbuffet1-1"].periodic(15, realtime_intensity, 0, 4).start()
            effects["spoilerbuffet1-2"].periodic(16, realtime_intensity, 0, 4).start()
            effects["spoilerbuffet2-1"].periodic(14, realtime_intensity, 90, 4).start()
            effects["spoilerbuffet2-2"].periodic(18, realtime_intensity, 90, 4).start()
        else:
            for effect in ["spoilerbuffet1-1", "spoilerbuffet1-2", "spoilerbuffet2-1", "spoilerbuffet2-2"]:
                effects[effect].stop(1000)

    def ac_update_tailhook_effect(self, telem_data):
        """
        Tailhook motion effect:
            Checks for presense of Tailhook telemetery and type of device.  If effect disabled or intensty set to 0,
            ensure effect is stopped and abort.
            Check for change in telemetry over the past 'delta_ms' miliseconds.  Delta insures effect doesn't flap on
            some aircraft with slow telemetry updates.
            When telemetry stops, check if hook is fully deployed or stored and play a short bump. Then stop the effect
        """
        hook = telem_data.get('TailHook', None)
        if hook is None: return
        if not self.is_joystick(): return

        if not self.tailhook_motion_effect_enabled or not self.tailhook_motion_intensity:
            effects.dispose('hookmovement')
            return

        if self.anything_has_changed("tailhook_value", hook, delta_ms=200):
            logging.debug(f"Hook Pos: {hook}")
            direction = 90 if self.is_pedals() else 0
            effects["hookmovement"].periodic(160, self.tailhook_motion_intensity, direction, EFFECT_SAWTOOTHUP).start()
        else:
            # play short bump when hook stowed or deployed, change direction based on state.  Only play if movement effect still playing
            if (hook == 0 or hook ==1) and effects['hookmovement'].started:
                dir = (1-hook) * 180
                effects['clunk'].periodic(10, utils.clamp((self.tailhook_motion_intensity * 2), 0, 1), dir, effect_type=EFFECT_SQUARE,duration=40).start()
            # stop the effect
            effects.dispose("hookmovement")

    def ac_update_fuelboom_effect(self, telem_data):
        """Fuel Boom motion effect using generic motion effect handler."""
        config = {
            'telem_key': 'FuelBoom',
            'change_key': 'fuelboom_value',
            'effect_names': ['boommovement'],
            'clunk_effects': ['clunk'],
            'enabled_attr': 'fuelboom_motion_effect_enabled',
            'intensity_attr': 'fuelboom_motion_intensity',
            'frequency': 150,
            'directions': [90 if self.is_pedals() else 0],
            'effect_type': EFFECT_SAWTOOTHDOWN,
            'delta_ms': 200,
            'clunk_duration': 40
        }
        self._create_motion_effect(telem_data, config)

    def ac_update_wingfold_effect(self, telem_data):
        """Wing Fold motion effect using generic motion effect handler."""
        config = {
            'telem_key': 'WingFold',
            'change_key': 'wingfold_value',
            'effect_names': ['wingfoldmovement_1', 'wingfoldmovement_2'],
            'clunk_effects': ['wingfoldclunk1', 'wingfoldclunk2'],
            'enabled_attr': 'wingfold_motion_effect_enabled',
            'intensity_attr': 'wingfold_motion_intensity',
            'frequency': 100,
            'directions': [45, 225],
            'effect_type': EFFECT_SAWTOOTHDOWN,
            'delta_ms': 200,
            'phase_offset': 90,
            'require_ground': True,
            'clunk_directions': [90, 270],
            'clunk_duration': 100
        }
        self._create_motion_effect(telem_data, config)

    def ac_update_wind_effect(self, telem_data):
        if not self.is_joystick(): return
        if not self.wind_effect_enabled:
            effects.dispose("wnd")
            return

        wind = telem_data.get("Wind", (0, 0, 0))
        wnd = math.sqrt(wind[0] ** 2 + wind[1] ** 2 + wind[2] ** 2)

        v = HPFs.get("wnd", 3).update(wnd)
        v = LPFs.get("wnd", 15).update(v)
        v = utils.clamp(v, 0, self.wind_effect_max_intensity)
        v = utils.clamp(v*self.wind_effect_scaling, 0.0,1.0)
        if v == 0:
            effects.dispose("wind")
            return
        logging.debug(f"Adding wind effect intensity:{v}")
        effects["wnd"].constant(v, utils.RandomDirectionModulator, 5).start()

    def ac_update_hydraulic_loss_effect(self, telem_data):

        telem_data['_hyd_factor'] = self.hydraulic_factor

        if not self.enable_hydraulic_loss_effect:
            return False
        hydraulic_sys = telem_data.get('HydSys', "n/a")
        hydraulic_pressure = telem_data.get('HydPress', 1)

        if not self.enable_damper_ovd or not self.enable_inertia_ovd or not self.enable_friction_ovd:
            self.flag_error("Hydraulic Loss effect enabled but damper/inertia/friction overrides not enabled - effect requires all three enabled with base values set")
            return False

        if hydraulic_sys == 'n/a':
            return False

        if isinstance(hydraulic_pressure, list):
            hydraulic_pressure = max(hydraulic_pressure)

        if isinstance(hydraulic_sys, int) and (hydraulic_sys == 1 or hydraulic_sys == 0):
            hydraulic_sys = bool(hydraulic_sys)

        if isinstance(hydraulic_sys, list):
            self.hydraulic_factor = max(hydraulic_sys)

        elif isinstance(hydraulic_sys, bool):
            if self._sim_is_dcs() and hydraulic_sys == True and hydraulic_pressure == 0:
                hydraulic_sys = False

            if hydraulic_sys == True:
                self.hydraulic_factor = self.step_value_over_time('hyd_factor', self.hydraulic_factor, 2500, 1, floatpoint=True)
            elif hydraulic_sys == False:
                self.hydraulic_factor = self.step_value_over_time('hyd_factor', self.hydraulic_factor, 2500, 0, floatpoint=True)

            # hydraulic_factor = int(hydraulic_sys)
            telem_data['_hydraulic_factor_test'] = self.hydraulic_factor
        else:
            self.hydraulic_factor = hydraulic_sys

        if self.hydraulic_factor >= self.hydraulic_loss_threshold:
            effects["hyd_loss_damper"].destroy()
            effects["hyd_loss_inertia"].destroy()
            effects["hyd_loss_friction"].destroy()
            self.damper_coeff = int(self.damper_force * 4096)
            self.inertia_coeff = int(self.inertia_force * 4096)
            self.friction_coeff = int(self.friction_force * 4096)
            return False

        damper = utils.scale(self.hydraulic_factor, (0, self.hydraulic_loss_threshold), (self.hydraulic_loss_damper, self.damper_force))
        inertia = utils.scale(self.hydraulic_factor, (0, self.hydraulic_loss_threshold), (self.hydraulic_loss_inertia, self.inertia_force))
        friction = utils.scale(self.hydraulic_factor, (0, self.hydraulic_loss_threshold), (self.hydraulic_loss_friction, self.friction_force))

        self.damper_coeff = utils.clamp(int(damper * 4096), 0, 4096)
        self.inertia_coeff = utils.clamp(int(inertia * 4096), 0, 4096)
        self.friction_coeff = utils.clamp(int(friction * 4096), 0, 4096)

        effects["damper"].destroy()
        effects["inertia"].destroy()
        effects["friction"].destroy()

        if not effects["hyd_loss_damper"].started or self.anything_has_changed('_hyd_loss_damper', self.damper_coeff):
            effects["hyd_loss_damper"].damper(self.damper_coeff, self.damper_coeff).start()
        if not effects["hyd_loss_inertia"].started or self.anything_has_changed('_hyd_loss_inertia', self.inertia_coeff):
            effects["hyd_loss_inertia"].inertia(self.inertia_coeff, self.inertia_coeff).start()
        if not effects["hyd_loss_friction"].started or self.anything_has_changed('_hyd_loss_friction', self.friction_coeff):
            effects["hyd_loss_friction"].friction(self.friction_coeff, self.friction_coeff).start()

        return True

    def ac_update_ffb_forces(self, telem_data):

        if self.enable_damper_ovd:
            if self.anything_has_changed('damper_value', self.damper_force) or not effects['damper'].started:
                force = utils.clamp(self.damper_force, 0.0, 1.0)
                effects["damper"].damper(int(4096*force), int(4096*force)).start()
        else:
            if effects['damper'].started:
                effects["damper"].destroy()

        if self.enable_inertia_ovd:
            if self.anything_has_changed('inertia_value', self.inertia_force) or not effects['inertia'].started:
                force = utils.clamp(self.inertia_force, 0.0, 1.0)
                effects["inertia"].inertia(int(4096*force), int(4096*force)).start()
        else:
            if effects['inertia'].started:
                effects["inertia"].destroy()

        if not self.friction_effect_overridden:
            if self.enable_friction_ovd:
                force = utils.clamp(self.friction_force, 0.0, 1.0)
                effects['friction'].name = 'friction'
                effects["friction"].friction(int(4096*force), int(4096*force)).start()
            else:
                if effects['friction'].started:
                    effects["friction"].destroy()

    ########################################
    ######                            ######
    ######    Prop Aircraft Effects   ######
    ######                            ######
    ########################################

    def ac_override_elevator_droop(self, telem_data):
        if not self.is_joystick():
            return
        if not self.elevator_droop_enabled or not self.elevator_droop_force:
            effects.dispose('elev_droop')
            return

        if telem_data['TAS'] < 20 * knots:
            force = utils.scale_clamp(telem_data['TAS'], (20 * knots, 0), (0, self.elevator_droop_force))
            effects['elev_droop'].constant(force, 180).start()
            logging.debug(f"override elevator:{force}")
        else:
            effects.dispose('elev_droop')

    def ac_update_aoa_effect(self, telem_data, minspeed=50*kmh, maxspeed=140*kmh):
        if not self.aoa_effect_enabled: return
        if not self.is_joystick(): return
        if self.spring_mode_is(self.SpringModeEnum.FBW) or telem_data.get("ACisFBW"): return

        aoa = telem_data.get("AoA", 0)
        tas = telem_data.get("TAS", 0)
        local_stall_aoa = self.stall_aoa

        if aoa:
            aoa = float(aoa)
            speed_factor = utils.scale_clamp(tas, (minspeed, maxspeed), (0, 1.0))
            mag = utils.scale_clamp(abs(aoa), (0, local_stall_aoa), (0, self.max_aoa_cf_force))
            mag *= speed_factor
            if (aoa > 0):
                dir = 0
            else:
                dir = 180

            telem_data["aoa_pull"] = mag
            logging.debug(f"AOA EFFECT:{mag}")
            effects["aoa"].constant(mag, dir).start()

    def ac_update_piston_engine_rumble(self, telem_data):
        legacy_voices = ("prop_rpm0-1", "prop_rpm0-2", "prop_rpm1-1", "prop_rpm1-2")
        physics_voices = ("prop_phys_1", "prop_phys_2", "prop_phys_3", "prop_phys_4",
                          "cyl_phys_1", "cyl_phys_2", "cyl_phys_3", "cyl_phys_4")
        if not self.engine_prop_rumble_enabled:
            effects.dispose(*legacy_voices, *physics_voices)
            return

        # Per-engine RPM. Most sims expose only one combined value; MSFS gives
        # PropRPM as a list. We drive one prop voice per engine when a list is
        # available, else just engine 1.
        if self._sim_is('DCS'):
            rpm_in = telem_data.get("ActualRPM", 0.0)
        elif self._sim_is('MSFS') or self._sim_is_xplane():
            rpm_in = telem_data.get("PropRPM", 0.0)
        elif self._sim_is('IL2'):
            rpm_in = telem_data.get("RPM", 0.0)
        else:
            logging.warning("Unknown sim trying to play Engine Rumble effect")
            rpm_in = 0.0

        if isinstance(rpm_in, list):
            per_engine = [float(r) for r in rpm_in if r is not None]
        else:
            per_engine = [float(rpm_in)]

        max_rpm = max(per_engine) if per_engine else 0.0
        if max_rpm < 5:
            effects.dispose(*legacy_voices, *physics_voices)
            return

        force_limit = max(self.engine_rumble_highrpm_intensity, self.engine_rumble_lowrpm_intensity)
        dynamic_rumble_intensity = utils.clamp(self.ac_calc_engine_intensity(max_rpm), 0, force_limit)

        if self.engine_rumble_mode == "physics":
            # Propeller blade-pass: prop_blade_count blades per prop revolution,
            # prop spinning at engine RPM × reduction ratio.
            blade_count = max(1, int(self.prop_blade_count))
            for idx, rpm in enumerate(per_engine[:4]):
                if rpm < 5:
                    effects.dispose(f"prop_phys_{idx+1}")
                    continue
                prop_rpm = rpm * float(self.prop_reduction_ratio)
                effects[f"prop_phys_{idx+1}"].physics(
                    rpm=prop_rpm, divisions=blade_count,
                    load=dynamic_rumble_intensity).start()
            # Cylinder-firing voice: only on if explicitly enabled (it can be
            # either a great or a too-busy effect depending on the engine).
            if self.cylinder_firing_enabled:
                cylinders = max(1, int(self.cylinder_count))
                # 4-stroke: cylinders/2 firings per crank rev. 2-stroke: cylinders.
                firings = (cylinders / 2.0) if self.is_4_stroke else float(cylinders)
                for idx, rpm in enumerate(per_engine[:4]):
                    if rpm < 5:
                        effects.dispose(f"cyl_phys_{idx+1}")
                        continue
                    effects[f"cyl_phys_{idx+1}"].physics(
                        rpm=rpm, divisions=firings,
                        load=dynamic_rumble_intensity * 0.7).start()
            else:
                effects.dispose("cyl_phys_1", "cyl_phys_2", "cyl_phys_3", "cyl_phys_4")
            # Make sure legacy voices are off.
            effects.dispose(*legacy_voices)
            return

        # ---- Legacy synthesis-mode path (continuous sine) ----
        frequency = max_rpm / 60.0
        median_modulation = 2
        frequency2 = frequency + median_modulation
        r1_modulation = utils.sine_point_in_time(3, 10000)
        r2_modulation = utils.sine_point_in_time(3, 17500, phase_offset_deg=45)
        if frequency > 0:
            effects["prop_rpm0-1"].periodic(frequency, dynamic_rumble_intensity, 0).start()
            effects["prop_rpm0-2"].periodic(frequency + r1_modulation, dynamic_rumble_intensity, 0).start()
            effects["prop_rpm1-1"].periodic(frequency2, dynamic_rumble_intensity, 90).start()
            effects["prop_rpm1-2"].periodic(frequency2 + r2_modulation, dynamic_rumble_intensity, 90).start()
        else:
            effects.dispose(*legacy_voices)
        # Make sure physics voices are off.
        effects.dispose(*physics_voices)

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
            #give some extra juice if RPM is very low (i.e. on engine start)
            interpolated_intensity = utils.scale(rpm, (0, min_rpm), (max_intensity*2, max_intensity))
        else:
            #update to use scaling function
            interpolated_intensity = utils.scale(rpm, (min_rpm, max_rpm), (max_intensity, min_intensity))
        logging.debug(f"rpm = {rpm} | rpm percent of range: {rpm_percentage} | interpolated intensity: {interpolated_intensity}")

        return interpolated_intensity

    ########################################
    ######                            ######
    ######    Jet Aircraft Effects    ######
    ######                            ######
    ########################################
    def ac_update_ab_effect(self, telem_data):
        if not self.afterburner_effect_intensity or not self.afterburner_effect_enabled:
            effects.dispose("ab_rumble_1_1", "ab_rumble_2_1")
            return

        frequency = 20
        median_modulation = 2
        modulation_pos = 2
        modulation_neg = 1
        frequency2 = frequency + median_modulation
        precision = 2
        afterburner_pos = telem_data.get("Afterburner", 0)
        if isinstance(afterburner_pos, list):
            afterburner_pos = max(afterburner_pos)

        r1_modulation = utils.sine_point_in_time(modulation_pos, 15000)
        r2_modulation = utils.sine_point_in_time(modulation_neg, 15000)

        if afterburner_pos and (self.anything_has_changed("Afterburner", afterburner_pos) or self.anything_has_changed("Modulation", r1_modulation)):
            # logging.debug(f"AB Effect Updated: LT={Left_Throttle}, RT={Right_Throttle}")
            intensity = self.afterburner_effect_intensity * afterburner_pos
            effects["ab_rumble_1_1"].periodic(frequency + r1_modulation, intensity, 0,effect_type=EFFECT_TRIANGLE ).start()
            # effects["ab_rumble_1_2"].periodic(frequency + r1_modulation, intensity, 0).start()
            effects["ab_rumble_2_1"].periodic(frequency + r1_modulation, intensity, 45,effect_type=EFFECT_TRIANGLE ).start()
            # effects["ab_rumble_2_2"].periodic(frequency2 + r2_modulation, intensity, 45, 4, phase=120,
            #                                   offset=60).start()
            # logging.debug(f"AB-Modul1= {r1_modulation} | AB-Modul2 = {r2_modulation}")
        elif afterburner_pos == 0:
            # logging.debug(f"Both Less: Eng1: {eng1} Eng2: {eng2}, effect= {Aircraft.effect_index_set}")
            effects.dispose("ab_rumble_1_1")
            # effects.dispose("ab_rumble_1_2")
            effects.dispose("ab_rumble_2_1")
            # effects.dispose("ab_rumble_2_2")

    def ac_update_jet_engine_rumble(self, telem_data):
        if not self.engine_jet_rumble_enabled or not self.jet_engine_rumble_intensity > 0:
            effects.dispose("je_rumble_1_1", "je_rumble_1_2", "je_rumble_2_1", "je_rumble_2_2")
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
            jet_eng_rpm = telem_data.get("EngPCT", 0)
        else:
            jet_eng_rpm = telem_data.get("EngRPM", 0)
        if type(jet_eng_rpm) == list:
            jet_eng_rpm = max(jet_eng_rpm)
       
        if jet_eng_rpm == 0:
            # logging.debug(f"Both Less: Eng1: {eng1} Eng2: {eng2}, effect= {Aircraft.effect_index_set}")
            effects.dispose("je_rumble_1_1", "je_rumble_1_2", "je_rumble_2_1", "je_rumble_2_2")
            return
        
        r1_modulation = utils.sine_point_in_time(3, 60000)
        r2_modulation = utils.sine_point_in_time(2, 42500, phase_offset_deg=0)
        intensity = self.jet_engine_rumble_intensity * (jet_eng_rpm / 100)
        intensity = utils.clamp(intensity, 0, 1)
        rt_freq = round(frequency + (10 * (jet_eng_rpm / 100)), 4)
        rt_freq2 = round(rt_freq + median_modulation, 4)
        effects["je_rumble_1_1"].periodic(rt_freq + r1_modulation, intensity, 0, effect_index).start()
        # effects["je_rumble_1_2"].periodic(rt_freq + r1_modulation, intensity, 0, effect_index).start()
        effects["je_rumble_2_1"].periodic(rt_freq2 + r2_modulation, intensity, 90, effect_index, phase=phase_offset).start()
        # effects["je_rumble_2_2"].periodic(rt_freq2 + r2_modulation, intensity, 90, effect_index, phase=phase_offset+30).start()
        logging.debug(f"JE-M1={r1_modulation}, F1-1={rt_freq}, F1-2={round(rt_freq + r1_modulation,4)} | JE-M2 = {r2_modulation}, F2-1={rt_freq2}, F2-2={round(rt_freq2 + r2_modulation, 4)} ")


    ########################################
    ######                            ######
    ######     Helicopter Effects     ######
    ######                            ######
    ########################################

    def ac_calc_etl_effect(self, telem_data, blade_ct=None):
        #  rotor = 245
        mod = telem_data.get("N")
        tas = telem_data.get("TAS", 0)
        WoW = sum(telem_data.get("WeightOnWheels"))
        if mod == "UH-60L":
            # UH60 always shows positive value for tailwheel
            WoW = telem_data.get("WeightOnWheels")[0] + telem_data.get("WeightOnWheels")[2]

        if self._sim_is_xplane():
            rotor = telem_data.get("PropRPM", 0)
            if isinstance(rotor, list):
                rotor = rotor[0]
        else:
            rotor = telem_data.get("RotorRPM", 0)
            if isinstance(rotor, list):
                rotor = max(rotor)
        if WoW > 0:
            # logging.debug("On the Ground, moving forward. Probably on a Ship! - Dont play effect!")
            effects.dispose("etlX", "etlY", "overspeedX", "overspeedY")
            return
        if blade_ct is None:
            blade_ct = 2
            rotor = 250

        self.etl_shake_frequency = (rotor / 75) * blade_ct
        self.overspeed_shake_frequency = self.etl_shake_frequency * 0.75

        etl_mid = (self.etl_start_speed + self.etl_stop_speed) / 2.0

        if (tas >= self.etl_start_speed and tas <= self.etl_stop_speed) and self.etl_effect_intensity and self.etl_effect_enable:
            shake = self.etl_effect_intensity * utils.gaussian_scaling(tas, self.etl_start_speed, self.etl_stop_speed, peak_percentage=0.5, curve_width=.7)
            shake = utils.clamp(shake, 0.0, 1.0)
            effects["etlY"].periodic(self.etl_shake_frequency, shake, 0).start()
            effects["etlX"].periodic(self.etl_shake_frequency + 4, shake, 90).start()
            logging.debug(f"Playing ETL shake (freq = {self.etl_shake_frequency}, intens= {shake})")
        else:
            effects.dispose("etlX", "etlY")

        if tas >= self.overspeed_shake_start and self.overspeed_effect_enable:
            shake = self.overspeed_shake_intensity * utils.non_linear_scaling(tas, self.overspeed_shake_start, self.overspeed_shake_start + 15, curvature=.7)
            shake = utils.clamp(shake, 0.0, 1.0)
            effects["overspeedY"].periodic(self.overspeed_shake_frequency, shake, 0).start()
            effects["overspeedX"].periodic(self.overspeed_shake_frequency + 4, shake, 90).start()
            logging.debug(f"Overspeed shake (freq = {self.etl_shake_frequency}, intens= {shake}) ")
        else:
            effects.dispose("overspeedX", "overspeedY")

    def ac_update_vrs_effect(self, telem_data):
        vs = telem_data.get("VerticalSpeed", 0)
        if self._sim_is_dcs():
            # spd = abs(telem_data.get("VlctVectors")[0])
            tas = telem_data.get("TAS")
            adj_tas = tas - abs(vs)
            spd = adj_tas
            telem_data['_adj_TAS'] = adj_tas
        else:
            spd = abs(telem_data.get('TAS', 0))
        wow = max(telem_data.get("WeightOnWheels", 1))
        # print(f"tas:{tas}, vs:{vs}, wow:{wow}")
        if not self.vrs_effect_enable or wow or spd > self.vrs_threshold_speed or vs > 0:
            # print("I'm out")
            effects.dispose("vrs_buffet", "vrs_buffet2")
            return

        if abs(vs) >= self.vrs_vs_onset:
            vs_factor = utils.scale(abs(vs), (self.vrs_vs_onset, self.vrs_vs_max), (0.0, self.vrs_effect_intensity))
            if spd == 0:
                spd_factor = 1
            else:
                spd_factor = utils.scale(spd, (spd*1.2, spd), (0,1))

            intensity = utils.clamp(vs_factor * spd_factor, 0, 1)

            effects["vrs_buffet"].periodic(10, intensity, utils.RandomDirectionModulator).start()
            effects['vrs_buffet2'].periodic(12, intensity, utils.RandomDirectionModulator).start()
        else:
            effects.dispose("vrs_buffet", "vrs_buffet2")

    def ac_update_heli_engine_rumble(self, telem_data, blade_ct=None):
        if not self.engine_rotor_rumble_enabled or not self.heli_engine_rumble_intensity:
            effects.dispose("rotor_rpm0-1", "rotor_rpm1-1",
                            "rotor_phys_main", "rotor_phys_tail")
            return
        if self._sim_is_xplane():
            rrpm = telem_data.get("PropRPM", 0)
            if isinstance(rrpm, list):
                rrpm = rrpm[0]
        else:
            rrpm = telem_data.get("RotorRPM", 0)
            if isinstance(rrpm, list):
                rrpm = max(rrpm)
        eng_rpm = telem_data.get("EngRPM", 0)
        if isinstance(eng_rpm, list):
            eng_rpm = max(eng_rpm)

        if blade_ct is None:
            blade_ct = 2
            rrpm = 250

        if rrpm < 5:
            effects.dispose("rotor_rpm0-1", "rotor_rpm1-1",
                            "rotor_phys_main", "rotor_phys_tail")
            return

        if self.engine_rumble_mode == "physics":
            # Phase-locked impulse train: one thump per blade pass. Amplitude
            # weighted by RPM proximity to nominal so spool-up reads as a
            # laboured chuff that fattens up as the rotor reaches operating
            # speed. The intensity knob keeps the same UX as the legacy mode.
            #
            # Use rrpm itself as the load proxy when collective isn't
            # available — at sim-pause the impulse train silences itself
            # because rrpm < 5 was already caught above.
            collective = telem_data.get("CollectivePos")
            if isinstance(collective, list):
                collective = collective[0] if collective else None
            if collective is None:
                # Fall back to RPM-as-load: spool-up sounds light, on-condition
                # rotor sounds full. Caps at 1.0 once rrpm exceeds nominal.
                nominal = max(1.0, getattr(self, "rotor_rpm_nominal", rrpm))
                collective = min(1.0, max(0.0, float(rrpm) / nominal))
            else:
                # Sims report collective in [0, 1] or [0, 100]; normalise.
                collective = float(collective)
                if collective > 1.5:
                    collective /= 100.0
                collective = max(0.0, min(1.0, collective))
            load = max(0.05, collective) * self.heli_engine_rumble_intensity
            effects["rotor_phys_main"].physics(rpm=rrpm, divisions=blade_ct,
                                                load=load).start()
            if self.tailrotor_enabled:
                tail_rpm = telem_data.get("TailRotorRPM")
                if tail_rpm is None:
                    tail_rpm = float(rrpm) * float(self.tailrotor_gear_ratio)
                effects["rotor_phys_tail"].physics(
                    rpm=tail_rpm, divisions=self.tailrotor_blade_count,
                    load=load * 0.6).start()
            else:
                effects.dispose("rotor_phys_tail")
            # Make sure legacy voices are off when physics is active.
            effects.dispose("rotor_rpm0-1", "rotor_rpm1-1")
            return

        # ---- Legacy synthesis-mode path (continuous sine) ----
        logging.debug(f"Engine Rumble: Blade_Ct={blade_ct}, RPM={rrpm}")
        frequency = float(rrpm) / 45 * blade_ct
        median_modulation = 2
        frequency2 = frequency + median_modulation
        if frequency > 0 and eng_rpm > 0:
            effects["rotor_rpm0-1"].periodic(frequency, self.heli_engine_rumble_intensity * .5, 0).start()  # vib on X axis
            effects["rotor_rpm1-1"].periodic(frequency2, self.heli_engine_rumble_intensity * .5, 90).start()  # vib on Y axis
        else:
            effects.dispose("rotor_rpm0-1", "rotor_rpm1-1")
        # Make sure physics voices are off in synthesis mode.
        effects.dispose("rotor_phys_main", "rotor_phys_tail")

    def check_master_button_press(self, button):
        # print(f"Checking {button} against {master_buttons}")
        return button in G.master_buttons

    def check_for_button_press(self, button):
        input_data = HapticEffect.device.get_input()

    def check_button_press(self, button=0, check_master=False):
        if not button:
            #button not set
            return False
        if check_master:
            return self.check_master_button_press(button)
        else:
            input_data = HapticEffect.device.get_input()
            return input_data.isButtonPressed(button)

    def ac_update_pedal_trim(self, telem_data):
        """Update the pedal trim effect based on telemetry data and user input.
        This method should be overridden in subclasses to implement specific pedal trim logic.
        """
        pass

    def ac_update_pedal_force_trim(self, telem_data, ft_active=True):
        if not self.is_pedals(): return

        input_data = HapticEffect.device.get_input()
        phys_x, phys_y = input_data.axisXY()

        force_trim_pressed = self.check_button_press(self.pedal_ft_release_button, self.pedal_ft_use_master_buttons)
        trim_reset_pressed = self.check_button_press(self.pedal_ft_reset_button, self.pedal_ft_use_master_buttons)


        if force_trim_pressed or not ft_active:
            if self.pedal_ft_damper_enabled:
                self.spring_x.set_coefficient(self.pedal_ft_damper_force)
            else:
                self.spring_x.set_coefficient(0)

            self.cpO_x = round(phys_x * 4096)
            self.spring_x.cpOffset = self.cpO_x
            return True

        if trim_reset_pressed or not self.pedal_trim_reset_complete:
            self.spring_x.set_coefficient(4096)
            self.cpO_x = self.step_value_over_time("center_x", self.cpO_x, 1000, 0)

            self.spring_x.cpOffset = self.cpO_x

            if self.cpO_x == 0:
                self.pedal_trim_reset_complete = True
            else:
                self.pedal_trim_reset_complete = False
            return True
        return False

    def ac_override_pedal_spring(self, telem_data):
        if not self.is_pedals(): return

        input_data = HapticEffect.device.get_input()
        phys_x, phys_y = input_data.axisXY()

        if self.spring_mode_is(self.SpringModeEnum.NONE):
            if effects['pedal_spring'].started:
                effects["pedal_spring"].stop()
            return

        if self.spring_mode_is(self.SpringModeEnum.NOSPRING):
            self.spring_x.set_coefficient(0)

        elif self.spring_mode_is(self.SpringModeEnum.STATIC):
            spring_coeff = utils.clamp(self.pedal_spring_gain, 0, 1.0)
            self.spring_x.set_coefficient(spring_coeff)
            if self.pedal_trimming_enabled and self._sim_is_dcs():
                self.ac_update_pedal_trim(telem_data)

        elif self.spring_mode_is(self.SpringModeEnum.FORCETRIM):
            if not self.ac_update_pedal_force_trim(telem_data):
                spring_coeff = utils.clamp(self.pedal_spring_gain, 0, 1.0)
                self.spring_x.set_coefficient(spring_coeff)

        elif self.spring_mode_is(self.SpringModeEnum.DYNAMIC) or self.spring_mode_is(self.SpringModeEnum.CUSTOM):
            tas = telem_data.get("TAS", 0)

            vs = self.aircraft_vs_speed

            vne = self.aircraft_vne_speed

            if vs > vne:
                self.flag_error(f"Dynamic pedal forces error: Vs speed ({vs}) is configured with a larger value than Vne ({vne}) - Invalid configuration")

            vs_coeff = utils.clamp(round(self.aircraft_vs_gain*4096), 0, 4096)
            vne_coeff = utils.clamp(round(self.aircraft_vne_gain*4096), 0, 4096)
            spr_coeff = utils.scale(tas, (vs, vne), (vs_coeff, vne_coeff))
            spr_coeff = round(spr_coeff * self.pedal_spring_gain)
            spr_coeff = utils.clamp(spr_coeff, 0, 4096)
            # print(f"coeff={spr_coeff}")
            self.spring_x.set_coefficient(spr_coeff)
            if self.pedal_trimming_enabled and self._sim_is_dcs():
                self.ac_update_pedal_trim(telem_data)
            # return
        spring = effects["pedal_spring"].spring()
        damper_coeff = round(utils.clamp((self.pedal_dampening_gain * 4096), 0, 4096))
        # self.damper = effects["pedal_damper"].damper(coef_x=damper_coeff).start()

        spring.setCondition(self.spring_x)
        spring.start(override=True)

    def ac_collective_force_trim_override(self, telem_data, spring):
        '''
        Generic effect enabling spring force and hardware trim for collective axis.
        '''

        if not self.is_collective(): return
        if not self.spring_mode_is(self.SpringModeEnum.FORCETRIM):
            # If feature disabled, ensure spring is stopped and abort
            effects['collective_ft'].stop()
            return


        dt = perftracker.get_time_delta('collective_ft_perf')
        self.telem_data['_coll_ft_dt'] = dt

        wow = sum(telem_data.get("WeightOnWheels", [1]))

        input_data = HapticEffect.device.get_input()
        _, y = input_data.axisXY()
        current_buttons = input_data.getPressedButtons()

        force_trim_active = telem_data.get('ForceTrimSW', True)

        if not force_trim_active:
            # Force trim is enabled, but the 'ForceTrimSW' flag is false, just move
            self.spring_y.set_coefficient(self.collective_ft_ovd_tr_damper)
            self.collective_ft_ovd_cp0_y = round(y * 4096)
            self.spring_y.set_offset(self.collective_ft_ovd_cp0_y)
            spring.setCondition(self.spring_y)
            return

        # decide what to do depending on which button is pressed
        if self.check_button_press(self.collective_ft_ovd_release, self.collective_ft_use_master_buttons):
            # use spring force as dampening.  Configured damper value applied as spring gain.  cpO will follow stick
            # as it is moved while spring force is enabled.
            # return from method so default spring gains do not get applied at the end of the method
            self.spring_y.set_coefficient(self.collective_ft_ovd_tr_damper)

            self.collective_ft_ovd_cp0_y = round(y * 4096)
            self.spring_y.set_offset(self.collective_ft_ovd_cp0_y)
            spring.setCondition(self.spring_y)
            spring.start(override=True)
            return

        elif self.check_button_press(self.collective_ft_ovd_reset, self.collective_ft_use_master_buttons):
            # if trim reset button pressed, set offsets back to 0
            # print("TRIM RESET")
            self.collective_ft_ovd_cp0_y = 4096
            self.spring_y.set_offset(self.collective_ft_ovd_cp0_y)
            spring.setCondition(self.spring_y)

        # calculate step size based on configured rate and delta time
        trim_step_size = self.collective_ft_ovd_trim_rate * dt

        self.telem_data['_coll_ft_step'] = trim_step_size


        if self.check_button_press(self.collective_ft_ovd_trim_down, self.collective_ft_use_master_buttons):
            # shift offset based on previously calculated step size.  Ensure value does not exceed limits
            # print("TRIM DOWN")
            self.collective_ft_ovd_cp0_y += trim_step_size
            self.collective_ft_ovd_cp0_y = utils.clamp(self.collective_ft_ovd_cp0_y, -4096, 4096)
            self.spring_y.set_offset(round(self.collective_ft_ovd_cp0_y))
        elif self.check_button_press(self.collective_ft_ovd_trim_up, self.collective_ft_use_master_buttons):
            # shift offset based on previously calculated step size.  Ensure value does not exceed limits
            # print("TRIM UP")
            self.collective_ft_ovd_cp0_y -= trim_step_size
            self.collective_ft_ovd_cp0_y = utils.clamp(self.collective_ft_ovd_cp0_y, -4096, 4096)
            self.spring_y.set_offset(round(self.collective_ft_ovd_cp0_y))


        self.telem_data['_coll_ft_trim_pos'] = round(self.collective_ft_ovd_cp0_y)

        # If trim release is not pressed, set spring gain based on user setting and start spring override
        self.spring_y.set_coefficient(self.collective_ft_ovd_spring_gain)

        spring.setCondition(self.spring_y)
        # ensure spring is started with override = true
        spring.start(override=True)

    def ac_modify_game_spring(self):
        if not self.spring_mode_is(self.SpringModeEnum.ADVANCED):
            self.spring_adjuster.stop()
            return
        # Verify the device firmware meets the minimum version required to execute this effect
        # Flag error and abort if not met
        supported = utils.check_min_firmware_version(G.device_firmware_version, "v1.0.18")
        if not supported:
            self.flag_error('The Advanced/Custom Spring Override requires firmware v1.0.18 or higher.\n'
                            f'The device is currently running version {G.device_firmware_version}\n'
                            f'Please update your device firmware!')
            return
        if self.adv_spr_gains == 'none':
            self.flag_error('Please open and configure the advanced spring gain settings')
            return

        gains = utils.get_gain_from_speed(self.adv_spr_gains, self.telem_data.get('IAS', 0))

        self.spring_adjuster.name = 'adv_spr'
        self.spring_adjuster_y.set_coefficient(gains.get('y', 0))
        self.spring_adjuster_x.set_coefficient(gains.get('x', 0))

        if self.adv_spr_use_hardware_trim:
            dt = perftracker.get_time_delta('override_spring_perf')
            trim_step_size = self.override_spring_trim_rate * dt
            # trim_step_size = 200 * dt
            self.telem_data['_ovrd_spr_step'] = trim_step_size
            self.telem_data['_ovrd_spr_dt'] = dt
            # evaluate UP or DOWN and then LEFT or RIGHT trims.  Allows movement on both axes simultaneously but not
            # accidental confliction of trying to move both directions on a single axis due to bad hat bindings
            input_data = HapticEffect.device.get_input()
            x, y = input_data.axisXY()
            current_buttons = input_data.getPressedButtons()

            if self.override_spring_trim_down and self.override_spring_trim_down in current_buttons:
                self.override_spring_cp0_y -= trim_step_size
            elif self.override_spring_trim_up and self.override_spring_trim_up in current_buttons:
                self.override_spring_cp0_y += trim_step_size

            if self.override_spring_trim_left and self.override_spring_trim_left in current_buttons:
                self.override_spring_cp0_x -= trim_step_size
            elif self.override_spring_trim_right and self.override_spring_trim_right in current_buttons:
                self.override_spring_cp0_x += trim_step_size

            self.override_spring_cp0_x = round(utils.clamp(self.override_spring_cp0_x, -4096, 4096))
            self.override_spring_cp0_y = round(utils.clamp(self.override_spring_cp0_y, -4096, 4096))
        else:
            self.override_spring_cp0_x = 0
            self.override_spring_cp0_y = 0
        offset = self.ac_update_gforce_effect(self.telem_data, adv_spr=True)  # Returns g force spring offset if effect enabled and in offset mode
        self.g_y_offset = offset if offset is not None else 0
        self.telem_data['_ovrd_spr_trim_pos'] = [round(self.override_spring_cp0_x), round(self.override_spring_cp0_y), self.g_y_offset]
        self.spring_adjuster_y.set_offset(round(self.override_spring_cp0_y + self.g_y_offset))
        self.spring_adjuster_x.set_offset(round(self.override_spring_cp0_x))

        self.spring_adjuster.setCondition(self.spring_adjuster_y)
        self.spring_adjuster.setCondition(self.spring_adjuster_x)
        self.spring_adjuster.start()

    def ac_set_deadzone(self):
        if not self.enable_deadzone and self.deadzone_active:
            if self.active_deadzone_pct != 0.0:
                HapticEffect.device.set_deadzone(0)
                self.active_deadzone_pct = 0.0
                logging.info('Disabling deadzone')
                self.deadzone_active = False
            return
        if self.active_deadzone_pct != self.deadzone_base_pct:
            dz = utils.clamp(round((self.deadzone_base_pct / 100) * 4096), 0, 4096)
            HapticEffect.device.set_deadzone(dz)
            self.active_deadzone_pct = self.deadzone_base_pct
            logging.info(f"Setting Deadzone to %{self.deadzone_base_pct}")
            self.deadzone_active = True

    def on_event(self, event, *args):
        pass

    def on_timeout(self):  # override me
        logging.info("Telemetry Timeout, stopping effects")
        # effects.foreach(lambda e: e.stop())
        for key, effect in effects.dict.items():
            effect: HapticEffect
            if self.keep_forces_on_pause:
                if effect.effect_type in [EFFECT_SPRING, EFFECT_DAMPER, EFFECT_INERTIA, EFFECT_FRICTION, EFFECT_SPRING_ADJUSTER]:
                    continue
            effect.stop()
        if self.deadzone_active:
            HapticEffect.device.set_deadzone(0)
            self.deadzone_updated = False
            self.deadzone_active = False

    def on_telemetry(self, telem_data): 
        aircraft_type = telem_data.get("AircraftClass", "Unknown")
        fx,fy = HapticEffect.device.get_input().forceXY()
        self.telem_data['ForceXY'] = [fx,fy]
        jx, jy = HapticEffect.device.get_input().axisXY()
        self.telem_data['JoyXY'] = [jx, jy]
        # the methods should decide if they want to run based on the telemetry data
        if aircraft_type == "JetAircraft":
            self.ac_update_ab_effect(telem_data)

        elif aircraft_type == "PropellerAircraft":
            self.ac_update_piston_engine_rumble(telem_data)

        self.ac_update_aoa_reduction_force_effect(telem_data)
        self.ac_update_gforce_effect(telem_data)
        self.ac_update_wind_effect(telem_data)
        self.ac_update_jet_engine_rumble(telem_data)

        if aircraft_type == "Helicopter":
            self.ac_calc_etl_effect(telem_data, blade_ct=self.rotor_blade_count)
            self.ac_update_heli_engine_rumble(telem_data, blade_ct=self.rotor_blade_count)
            self.ac_update_vrs_effect(telem_data)
        else:
            # not helicopter
            vs0 = self.aircraft_vs_speed
            vne = self.aircraft_vne_speed
            # print(f"Got Vs0={vs0}, Vne={vne}")
            self.ac_update_aoa_effect(telem_data, minspeed=vs0, maxspeed=vne)

        if self.is_joystick():
            self.ac_override_elevator_droop(telem_data)

        if self.is_pedals():
            if not self._sim_is_msfs() and not self._sim_is_xplane():
                self.ac_override_pedal_spring(telem_data)


        self.ac_update_buffeting(telem_data)
        self.ac_update_cm_weapons(telem_data)

        hyd_loss = self.ac_update_hydraulic_loss_effect(telem_data)
        if not hyd_loss: 
            self.ac_update_ffb_forces(telem_data)
        
        self.ac_modify_game_spring()
        self.ac_set_deadzone()

        self.ac_update_tailhook_effect(telem_data)
        self.ac_update_fuelboom_effect(telem_data)
        self.ac_update_wingfold_effect(telem_data)
        self.ac_update_touchdown_effect(telem_data)
        self.ac_update_runway_rumble(telem_data)
        self.ac_update_decel_effect(telem_data)

        self.ac_update_speed_brakes(telem_data)
        self.ac_update_landing_gear(telem_data)
        self.ac_update_flaps(telem_data)
        self.ac_update_canopy(telem_data)
        self.ac_update_spoilers(telem_data)

    # Helper methods for code reuse
    def _get_random_direction(self):
        """Get a random direction for weapon effects based on device type."""
        import random
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
