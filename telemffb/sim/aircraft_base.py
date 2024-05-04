import logging
import time
import math
import random
import telemffb.utils as utils
from typing import List, Dict
# from utils import clamp, HighPassFilter, Derivative, Dispenser

from telemffb.hw.ffb_rhino import EFFECT_TRIANGLE, HapticEffect, FFBReport_SetCondition

# by accessing effects dict directly new effects will be automatically allocated
# example: effects["myUniqueName"]
effects: Dict[str, HapticEffect] = utils.Dispenser(HapticEffect)

# Highpass filter dispenser
HPFs: Dict[str, utils.HighPassFilter] = utils.Dispenser(utils.HighPassFilter)

# Lowpass filter dispenser
LPFs: Dict[str, utils.LowPassFilter] = utils.Dispenser(utils.LowPassFilter)

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
    aoa_buffet_freq = 13

    buffeting_intensity: float = 0.2  # peak AoA buffeting intensity  0 to disable
    buffet_aoa: float = 10.0  # AoA when buffeting starts
    stall_aoa: float = 15.0  # Stall AoA
    aoa_effect_enabled: int = 1

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

    spoiler_motion_intensity: float = 0.0  # peak vibration intensity when spoilers is moving, 0 to disable
    spoiler_buffet_intensity: float = 0.15  # peak buffeting intensity when spoilers deployed,  0 to disable

    aoa_buffeting_enabled: bool = True
    buffeting_intensity : float = 0.2               # peak AoA buffeting intensity  0 to disable
    buffet_aoa : float          = 10.0              # AoA when buffeting starts
    stall_aoa : float           = 15.0              # Stall AoA
    wind_effect_enabled : int = 0
    wind_effect_scaling: int = 0
    wind_effect_max_intensity: int = 0

    aoa_buffeting_enabled: bool = True
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

    ####
    #### Beta effects - set to 1 to enable
    gforce_effect_invert_force = 0  # 0=disabled(default),1=enabled (case where "180" degrees does not equal "away from pilot")
    gforce_effect_enable = 0
    gforce_effect_enable_areyoureallysure = 0
    gforce_effect_curvature = 2.2
    gforce_effect_max_intensity = 1.0
    gforce_min_gs = 1.5  # G's where the effect starts playing
    gforce_max_gs = 5.0  # G limit where the effect maxes out at strength defined in gforce_effect_max_intensity

    gear_motion_effect_enabled: bool = True
    gear_motion_intensity: float = 0.12
    gear_buffet_effect_enabled: bool = True
    gear_buffet_intensity: float = 0.15     # peak buffeting intensity when gear down during flight,  0 to disable

    ####
    #### Beta effects - set to 1 to enable
    deceleration_effect_enable = 0
    deceleration_effect_enable_areyoureallysure = 0
    deceleration_max_force = 0.5
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

    gforce_effect_enable : bool = False

    flaps_motion_intensity : float = 0.12      # peak vibration intensity when flaps are moving, 0 to disable
    flaps_buffet_intensity : float = 0.0      # peak buffeting intensity when flaps are deployed,  0 to disable

    canopy_motion_intensity : float = 0.12      # peak vibration intensity when canopy is moving, 0 to disable
    canopy_buffet_intensity : float = 0.0      # peak buffeting intensity when canopy is open during flight,  0 to disable

    max_aoa_cf_force: float = 0.2  # CF force sent to device at %stall_aoa
    elevator_droop_enabled: bool = False
    elevator_droop_force: float = 0.0
    aircraft_is_fbw: bool = False

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

    pedal_spring_mode = 'Static Spring'  ## 0=DCS Default | 1=spring disabled (Heli)), 2=spring enabled at %100 (FW)
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

    etl_start_speed = 6.0 # m/s
    etl_stop_speed = 22.0 # m/s
    etl_effect_intensity = 0.2 # [ 0.0 .. 1.0]
    etl_shake_frequency = 14.0 # value has been deprecated in favor of rotor RPM calculation
    overspeed_shake_start = 70.0 # m/s
    overspeed_shake_intensity = 0.2
    heli_engine_rumble_intensity = 0.12

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
        self._ipc_telem = {}
        self.hydraulic_factor = 0.000
        #clear any existing effects
        effects.clear()

        self.spring_x = FFBReport_SetCondition(parameterBlockOffset=0)
        self.spring_y = FFBReport_SetCondition(parameterBlockOffset=1)

    def step_value_over_time(self, key, value, timeframe_ms, dst_val, floatpoint=False):
        '''
        This function creates an entry in the  stepper dictionary which can be used to track the progress of driving a
        value from "a to b" over a period of time across multiple passes through the effects loop.
        '''

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

        step_size = (iteration_ms / time_to_go) * delta_to_go  # calculate step size required to reach target at time

        if data['value'] == data['dst_val']:  # if we have reached the dst value, delete the key and return the value
            del self.stepper_dict[key]
            return data['value']

        elapsed_time_ms = (current_time_ms - data['start_time'])

        if elapsed_time_ms >= timeframe_ms:  # if the elapsed time is greater than the given timeframe, return the destination value
            data['value'] = data['dst_val']
            return data['dst_val']

        val = value + step_size

        if not floatpoint:  # if floatpoint is not specified, return a rounded integer value
            val = round(val)
        data['value'] = val
        # print(f"value out = {data['value']}")
        return data['value']

    def apply_settings(self, settings_dict):
        for k, v in settings_dict.items():
            if k in ["type"]: continue
            if getattr(self, k, None) is None and k != 'vpconf' and 'dummy' and 'command_runner' not in k:
                logging.info(f"WARNING: Unknown parameter {k} in config")
                continue
            logging.info(f"set {k} = {v}")
            setattr(self, k, v)

    def has_changed(self, item: str, delta_ms=0, data=None) -> bool:
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
    
    def is_joystick(self):
        return self._telem_data.get("FFBType", "joystick") == "joystick"
    
    def is_pedals(self):
        return self._telem_data.get("FFBType") == "pedals"

    def is_collective(self):
        return self._telem_data.get("FFBType") == "collective"


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
        if self._telem_data.get("src") == "MSFS":
            return 1
        else:
            return 0

    def _sim_is_xplane(self):
        if self._telem_data.get('src') == "XPLANE":
            return True
        else:
            return False

    def _sim_is_dcs(self, *unused):
        if self._telem_data.get("src") == "DCS":
            return 1
        else:
            return 0
    def _sim_is(self, sim, *unused):
        if self._telem_data.get('src') == sim:
            return 1
        else:
            return 0

    ########################################
    ######                            ######
    ######  Generic Aircraft Effects  ######
    ######                            ######
    ########################################
    def _update_touchdown_effect(self, telem_data):
        """Generates a g-based force upon landing or as a result of large bumps"""

        max_force = 0.5
        max_g = 2
        if self.is_collective() or self.is_pedals():
            return
        if self._sim_is("DCS") or self._sim_is("IL2"):
            gs = round(telem_data.get("ACCs")[1] - 1, 2)  # subtract nominal G to align with zero based data from MSFS
        elif self._sim_is("MSFS") or self._sim_is("XPLANE"):
            gs = round(telem_data.get("AccBody")[1], 2)
        else:
            return

        if not self.touchdown_effect_enabled:
            effects.dispose("touchdown")
            return
        on_ground = telem_data.get("SimOnGround", 0)
        if not on_ground:
            effects.dispose("touchdown")
            return
        force = round(utils.scale_clamp(gs, (0, self.touchdown_effect_max_gs), (0,self.touchdown_effect_max_force)), 2)

        logging.debug(f"Touchdown Effect: Realtime Gs: {gs}, Force:{force}")
        # telem_data["_gs"] = gs
        # telem_data["_force"] = force
        effects['touchdown'].constant(force, 180).start()


    def _update_runway_rumble(self, telem_data):
        """Add wheel based rumble effects for immersion
        Generates bumps/etc on touchdown, rolling, field landing etc
        """
        if self.is_collective(): return
        if not self.runway_rumble_intensity or not self.runway_rumble_enabled:
            effects.dispose("runway0")
            effects.dispose("runway1")
            return

        WoW = telem_data.get("WeightOnWheels", (0, 0, 0))  # left, nose, right - wheels
        # get high pass filters for wheel shock displacement data and update with latest data
        hp_f_cutoff_hz = 3
        v1 = HPFs.get("center_wheel", hp_f_cutoff_hz).update((WoW[1])) * self.runway_rumble_intensity
        v2 = HPFs.get("side_wheels", hp_f_cutoff_hz).update(WoW[0] - WoW[2]) * self.runway_rumble_intensity

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
            effects.dispose("runway0")
            effects.dispose("runway1")

    def _gforce_effect(self, telem_data):
        if not self.is_joystick() or not self.gforce_effect_enable:
            effects.dispose("gforce")
            return
        
        if sum(telem_data.get("WeightOnWheels")):
            effects.dispose("gforce")
            return
        if not telem_data.get("TAS", 0):
            effects.dispose("gforce")
            return

        # gforce_effect_enable = 1
        gneg = -1.0
        gmin = self.gforce_min_gs
        gmax = self.gforce_max_gs
        direction = 180
        # if not gforce_effect_enable:
        #     return
        if self._sim_is("DCS") or self._sim_is("IL2"):
            gs: float = telem_data.get("ACCs")[1]
        elif self._sim_is("MSFS"):
            gs: float = telem_data.get("G")

        #gs = self.smoother.get_average("gs", gs, window_size=10)

        logging.debug(f"GS={gs}, AVG_Z_GS={gs}")
        if gs < gmin:
            effects["gforce"].stop()
            # effects.dispose("gforce_damper")
            return
        # g_factor = round(utils.scale(z_gs, (gmin, gmax), (0, self.gforce_effect_max_intensity)), 4)
        if self.gforce_effect_invert_force: 
            direction = 0
        g_factor = round(utils.non_linear_scaling(gs, gmin, gmax, curvature=self.gforce_effect_curvature), 4)

        derivative_hz = 5 # derivative lpf filter -3db Hz
        derivative_k = 0.1 # derivative gain value, or damping ratio

        dGs = getattr(self, "_dGs", None)
        if not dGs: dGs = self._dGs = utils.Derivative(derivative_hz)
        dGs.lpf.cutoff_freq_hz = derivative_hz

        g_deriv = - dGs.update(g_factor) * derivative_k
        
        #telem_data["g_deriv"] = g_deriv # uncomment to debug derivative
        #telem_data["g_factor"] = g_factor # uncomment to debug derivative
        g_factor += g_deriv 
        #telem_data["g_factor'"] = g_factor # uncomment to debug derivative

        g_factor = utils.clamp(g_factor, 0.0, 1.0)
        effects["gforce"].constant(g_factor, direction).start()
        #  effects["gforce_damper"].damper(coef_y=1024).start()

        logging.debug(f"G's = {gs} | gfactor = {g_factor}")

    def _aoa_reduction_force_effect(self, telem_data):
        if not self.aoa_reduction_effect_enabled:
            return
        if not self.is_joystick():
            return
        if sum(telem_data.get("WeightOnWheels")):
            effects.dispose("crit_aoa")
            return
        if not telem_data.get("TAS", 0):
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
    
    def _decel_effect(self, telem_data):
        if not self.deceleration_effect_enable or not self.is_joystick(): 
            effects.dispose("decel")
            return

        if self._sim_is("DCS") or self._sim_is("IL2"):
            y_gs = telem_data.get("ACCs")[0]
        elif self._sim_is("MSFS"):
            y_gs = telem_data.get("AccBody")[2]
        elif self._sim_is_xplane():
            y_gs = -telem_data.get("Gaxil")
        if not self.anything_has_changed("decel", y_gs):
            return
        if not sum(telem_data.get("WeightOnWheels")):
            effects.dispose("decel")
            return
        if not telem_data.get("TAS", 0):
            effects.dispose("decel")
            return
        avg_y_gs = self.smoother.get_average("y_gs", y_gs, sample_size=8)
        max_gs = self.deceleration_max_force
        if avg_y_gs < -0.1:
            if abs(avg_y_gs) > max_gs:
                avg_y_gs = -max_gs
            logging.debug(f"y_gs = {y_gs} avg_y_gs = {avg_y_gs}")
            effects["decel"].constant(abs(avg_y_gs), 180).start()
        else:
            effects.dispose("decel")

    def _calc_buffeting(self, aoa, speed, telem_data) -> tuple:
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

    def _update_buffeting(self, telem_data: dict):
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
            flaps = utils.average(telem_data.get("Flaps", 0)) * 0.2 # flaps down increases stall threshold by 20%
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

    def _update_drag_buffet(self, telem_data: dict, type: str):
        drag_buffet_threshold = 100  # indicated TAS via telemetry
        tas = telem_data.get("TAS", 0)
        if tas < drag_buffet_threshold:
            return 0

    def _update_cm_weapons(self, telem):
        payload = telem.get("PayloadInfo")
        gun = telem.get("Gun")
        flares = telem.get("Flares")
        chaff = telem.get("Chaff")
        if self.anything_has_changed("PayloadInfo", payload) and self.weapon_release_effect_enabled:
            # If effect direction is set to random (-1) in ini file, randomize direction - else, use configured direction (default=45)
            if self.weapon_effect_direction == -1:
                # Init random number for effect direction
                random.seed(time.perf_counter())
                random_weapon_release_direction = random.randint(0, 359)
                logging.info(f"Payload Effect Direction is randomized: {random_weapon_release_direction} deg")
                effects["payload_rel"].periodic(10, self.weapon_release_intensity, random_weapon_release_direction, duration=80).start(force=True)
            else:
                effects["payload_rel"].periodic(10, self.weapon_release_intensity, self.weapon_effect_direction, duration=80).start(force=True) # force sending the start command to the device
        elif not self.anything_has_changed("PayloadInfo", payload, delta_ms=160) or not self.weapon_release_effect_enabled:
            effects["payload_rel"].stop ()

        if self.anything_has_changed("Gun", gun) and self.gunfire_effect_enabled:
            # If effect direction is set to random (-1) in ini file, randomize direction - else, use configured direction (default=45)
            if self.weapon_effect_direction == -1:
                # Init random number for effect direction
                random.seed(time.perf_counter())
                random_weapon_release_direction = random.randint(0, 359)
                logging.info(f"Gun Effect Direction is randomized: {random_weapon_release_direction} deg")
                effects["gunfire"].periodic(10, self.gun_vibration_intensity, random_weapon_release_direction, duration=80).start(force=True)
            else:
                effects["gunfire"].periodic(10, self.gun_vibration_intensity, self.weapon_effect_direction, duration=80).start(force=True)
        elif not self.anything_has_changed("Gun", gun, delta_ms=160) or not self.gunfire_effect_enabled:
            effects["gunfire"].stop()

        if (self.anything_has_changed("Flares", flares) or self.anything_has_changed("Chaff", chaff)) and self.countermeasure_effect_enabled:
            # If effect direction is set to random (-1) in ini file, randomize direction - else, use configured direction (default=45)
            if self.weapon_effect_direction == -1:
                # Init random number for effect direction
                random.seed(time.perf_counter())
                random_weapon_release_direction = random.randint(0, 359)
                logging.info(f"CM Effect Direction is randomized: {random_weapon_release_direction} deg")
                effects["cm"].periodic(50, self.cm_vibration_intensity, random_weapon_release_direction, duration=80).start(force=True)
            else:
                effects["cm"].periodic(50, self.cm_vibration_intensity, self.weapon_effect_direction, duration=80).start(force=True)
        if not (self.anything_has_changed("Flares", flares, delta_ms=160) or self.anything_has_changed("Chaff", chaff, delta_ms=160)) or not self.countermeasure_effect_enabled:
            effects["cm"].stop()

    def _update_flaps(self, flapspos):

        # flapspos = data.get("Flaps")
        if self.anything_has_changed("Flaps", flapspos, delta_ms=100) and self.flaps_motion_intensity > 0 and self.flaps_motion_effect_enabled:
            logging.debug(f"Flaps Pos: {flapspos}")
            effects["flapsmovement"].periodic(180, self.flaps_motion_intensity, 0, 3).start()
        else:
            effects["flapsmovement"].stop(destroy_after=5000)

    def _update_canopy(self, canopypos):

        # canopypos = self._telem_data.get("canopy_value", 0)
        if self.anything_has_changed("Canopy", canopypos, delta_ms=300) and self.canopy_motion_intensity > 0 and self.canopy_motion_effect_enabled:
            logging.debug(f"Canopy Pos: {canopypos}")
            effects["canopymovement"].periodic(120, self.canopy_motion_intensity, 0, 3).start()
        else:
            effects["canopymovement"].stop(destroy_after=5000)

    def _update_landing_gear(self, gearpos, tas):
        if self._sim_is_xplane():
            self.gear_buffet_speed_low = 0.9 * self.telem_data.get("Vle", 10000) #set stupid high in case of telemetry failure
            self.gear_buffet_speed_high = self.gear_buffet_speed_low * 1.3


        rumble_freq = self.gear_buffet_freq

        if self.anything_has_changed("gear_value", gearpos, 50) and self.gear_motion_intensity > 0 and self.gear_motion_effect_enabled:
            logging.debug(f"Landing Gear Pos: {gearpos}")
            effects["gearmovement"].periodic(150, self.gear_motion_intensity, 0, 3).start()
            effects["gearmovement2"].periodic(150, self.gear_motion_intensity, 45, 3, phase=120).start()
        else:
            effects.dispose("gearmovement")
            effects.dispose("gearmovement2")

        if (tas > self.gear_buffet_speed_low and gearpos > .1) and self.gear_buffet_intensity > 0 and self.gear_buffet_effect_enabled:
            # calculate insensity based on deployment percentage
            # intensity will go from 0 to %100 configured between spd_thresh_low and spd_thresh_high

            realtime_intensity = utils.scale(tas, (self.gear_buffet_speed_low, self.gear_buffet_speed_high),(0, self.gear_buffet_intensity)) * gearpos
            effects["gearbuffet"].periodic(rumble_freq, realtime_intensity, 0, 4).start()
            effects["gearbuffet2"].periodic(rumble_freq, realtime_intensity, 90, 4).start()
            logging.debug(f"PLAYING GEAR RUMBLE intensity:{realtime_intensity}")
        else:
            effects.dispose("gearbuffet")
            effects.dispose("gearbuffet2")

    def _update_speed_brakes(self, spdbrk, tas, spd_thresh=70):
        if self._telem_data.get("AircraftClass", "GenericAircraft") == 'Helicopter':
            return

        if self.anything_has_changed("speedbrakes_value", spdbrk, 50) and self.speedbrake_motion_intensity > 0 and self.speedbrake_motion_effect_enabled:
            logging.debug(f"Speedbrake Pos: {spdbrk}")
            effects["speedbrakemovement"].periodic(180, self.speedbrake_motion_intensity, 0, 3).start()
        else:
            effects.dispose("speedbrakemovement")

        if tas > spd_thresh and spdbrk > .1 and self.speedbrake_buffet_intensity > 0 and self.speedbrake_buffet_effect_enabled:
            # calculate insensity based on deployment percentage
            realtime_intensity = self.speedbrake_buffet_intensity * spdbrk
            effects["speedbrakebuffet"].periodic(13, realtime_intensity, utils.RandomDirectionModulator).start()
            # effects["speedbrakebuffet2"].periodic(13, realtime_intensity, 45, 4).start()
            logging.debug(f"PLAYING SPEEDBRAKE RUMBLE intensity:{realtime_intensity}")
        else:
            effects.dispose("speedbrakebuffet")
            effects.dispose("speedbrakebuffet2")

    def _update_spoiler(self, spoiler, tas, spd_thresh_low=25, spd_thresh_hi=60):
        if self._telem_data.get("AircraftClass", "GenericAircraft") == 'Helicopter':
            return

        # tas = self._telem_data.get("TAS",0)
        tas_intensity = utils.clamp_minmax(utils.scale(tas, (spd_thresh_low, spd_thresh_hi), (0.0, 1.0)), 1.0)

        # spoiler = self._telem_data.get("Spoilers", 0)
        if spoiler == 0 or spoiler == None:
            effects.dispose("spoilermovement")
            effects.dispose("spoilermovement2")
            return
        # average all spoiler values together
        if isinstance(spoiler, list):
            if "F-14" in self._telem_data.get("N") and self._telem_data.get('src') == 'DCS':
                # give %85 weight to inner spoilers for intensity calculation
                spoiler_inner = (spoiler[1], spoiler[2])
                spoiler_outer = (spoiler[0], spoiler[3])
                spoiler = (0.85 * sum(spoiler_inner) + 0.15 * sum(spoiler_outer)) / 2
            else:
                spoiler = sum(spoiler) / len(spoiler)

        if self.spoiler_motion_intensity > 0 and self.spoiler_motion_intensity > 0 and self.spoiler_motion_effect_enabled:
            if self.anything_has_changed("Spoilers", spoiler):
                logging.debug(f"Spoilers Pos: {spoiler}")
                effects["spoilermovement"].periodic(118, self.spoiler_motion_intensity, 0, 4).start()
                effects["spoilermovement2"].periodic(118, self.spoiler_motion_intensity, 90, 4).start()
            else:
                logging.debug("Destroying Spoiler Effects")
                effects.dispose("spoilermovement")
                effects.dispose("spoilermovement2")

        if tas > spd_thresh_low and spoiler > .1 and self.spoiler_buffet_intensity > 0 and self.spoiler_buffet_effect_enabled:
            # calculate insensity based on deployment percentage
            realtime_intensity = self.spoiler_buffet_intensity * spoiler * tas_intensity
            logging.debug(f"PLAYING SPOILER RUMBLE | intensity: {realtime_intensity}, d-factor: {spoiler}, s-factor: {tas_intensity}")
            effects["spoilerbuffet1-1"].periodic(15, realtime_intensity, 0, 4).start()
            effects["spoilerbuffet1-2"].periodic(16, realtime_intensity, 0, 4).start()
            effects["spoilerbuffet2-1"].periodic(14, realtime_intensity, 90, 4).start()
            effects["spoilerbuffet2-2"].periodic(18, realtime_intensity, 90, 4).start()
        else:
            effects.dispose("spoilerbuffet1-1")
            effects.dispose("spoilerbuffet1-2")
            effects.dispose("spoilerbuffet2-1")
            effects.dispose("spoilerbuffet2-2")

    def _update_wind_effect(self, telem_data):
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

    def _update_hydraulic_loss_effect(self, telem_data):

        telem_data['_hyd_factor'] = self.hydraulic_factor

        if not self.enable_hydraulic_loss_effect:
            return False
        hydraulic_sys = telem_data.get('HydSys', "n/a")
        hydraulic_pressure = telem_data.get('HydPress', 1)

        if not self.enable_damper_ovd or not self.enable_inertia_ovd or not self.enable_friction_ovd:
            logging.warning("Hydraulic Loss effect enabled but damper/inertia/friction overrides not enabled - effect requires all three enabled with base values set")
            self.telem_data["error"] = 1
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
    def _old_update_hydraulic_loss_effect(self, telem_data):
        if not self.enable_hydraulic_loss_effect:
            return False
        hydraulic_sys = telem_data.get('HydSys', "n/a")
        hydraulic_pressure = telem_data.get('HydPress', 1)

        if hydraulic_sys == 'n/a':
            return False
        if not self.enable_damper_ovd or not self.enable_inertia_ovd or not self.enable_friction_ovd:
            logging.warning("Hydraulic Loss effect enabled but damper/inertia/friction overrides not enabled - effect requires all three enabled with base values set")
            self.telem_data["error"] = 1
            return False



        if isinstance(hydraulic_pressure, list):
            hydraulic_pressure = max(hydraulic_pressure)

        if isinstance(hydraulic_sys, list):
            hydraulic_factor = max(hydraulic_sys)

        elif isinstance(hydraulic_sys, bool):
            if self._sim_is_dcs() and hydraulic_sys == True and hydraulic_pressure == 0:
                hydraulic_sys = False
            hydraulic_factor = int(hydraulic_sys)
        else:
            hydraulic_factor = hydraulic_sys


        if isinstance(hydraulic_sys, bool):
            if hydraulic_factor == False:
                self.damper_coeff = self.step_value_over_time('hyd_loss_damper', self.damper_coeff, 5000, int(self.hydraulic_loss_damper * 4096))
                self.inertia_coeff = self.step_value_over_time('hyd_loss_inertia', self.inertia_coeff, 5000, int(self.hydraulic_loss_inertia * 4096))
                self.friction_coeff = self.step_value_over_time('hyd_loss_friction', self.friction_coeff, 5000, int(self.hydraulic_loss_friction * 4096))
            else:
                if (not effects['damper'].started or not effects['inertia'].started or not effects['friction'].started) and (self.damper_coeff > int(self.damper_force * 4096) or self.inertia_coeff > int(self.inertia_force * 4096) or self.friction_coeff > int(self.friction_force * 4096)):
                    self.damper_coeff = self.step_value_over_time('hyd_loss_damper', self.damper_coeff, 10000, int(self.damper_force * 4096))
                    self.inertia_coeff = self.step_value_over_time('hyd_loss_inertia', self.inertia_coeff, 10000, int(self.inertia_force * 4096))
                    self.friction_coeff = self.step_value_over_time('hyd_loss_friction', self.friction_coeff, 10000, int(self.friction_force * 4096))
                else:
                    effects["hyd_loss_damper"].destroy()
                    effects["hyd_loss_inertia"].destroy()
                    effects["hyd_loss_friction"].destroy()
                    self.damper_coeff = int(self.damper_force * 4096)
                    self.inertia_coeff = int(self.inertia_force * 4096)
                    self.friction_coeff = int(self.friction_force * 4096)
                    return False

        else:

            if hydraulic_factor >= self.hydraulic_loss_threshold:
                effects["hyd_loss_damper"].destroy()
                effects["hyd_loss_inertia"].destroy()
                effects["hyd_loss_friction"].destroy()
                self.damper_coeff = int(self.damper_force * 4096)
                self.inertia_coeff = int(self.inertia_force * 4096)
                self.friction_coeff = int(self.friction_force * 4096)
                return False

            damper = utils.scale(hydraulic_factor, (0, self.hydraulic_loss_threshold), (self.hydraulic_loss_damper, self.damper_force))
            inertia = utils.scale(hydraulic_factor, (0, self.hydraulic_loss_threshold), (self.hydraulic_loss_inertia, self.inertia_force))
            friction = utils.scale(hydraulic_factor, (0, self.hydraulic_loss_threshold), (self.hydraulic_loss_friction, self.friction_force))

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



    def _update_ffb_forces(self, telem_data):

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

        if self.enable_friction_ovd:
            if self.anything_has_changed('friction_value', self.friction_force) or not effects['friction'].started:
                force = utils.clamp(self.friction_force, 0.0, 1.0)
                effects["friction"].friction(int(4096*force), int(4096*force)).start()
        else:
            if effects['friction'].started:
                effects["friction"].destroy()


    ########################################
    ######                            ######
    ######    Prop Aircraft Effects   ######
    ######                            ######
    ########################################

    def override_elevator_droop(self, telem_data):
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

    def _update_aoa_effect(self, telem_data, minspeed=50*kmh, maxspeed=140*kmh):
        if not self.is_joystick(): return
        if self.aircraft_is_fbw or telem_data.get("ACisFBW"): return
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

    def update_piston_engine_rumble(self, telem_data):
        if not self.engine_prop_rumble_enabled:
            effects.dispose("prop_rpm0-1")
            effects.dispose("prop_rpm0-2")
            effects.dispose("prop_rpm1-1")
            effects.dispose("prop_rpm1-2")
            return

        if self._sim_is('DCS'):
            rpm = telem_data.get("ActualRPM", 0.0)
        elif self._sim_is('MSFS') or self._sim_is_xplane():
            rpm = telem_data.get("PropRPM", 0.0)
        elif self._sim_is('IL2'):
            rpm = telem_data.get("RPM", 0.0)
        else:
            logging.warning("Unknown sim trying to play Engine Rumble effect")
            rpm = 0.0
        
        if type(rpm) == list:
            rpm = max(rpm)

        if rpm < 5:
            effects.dispose("prop_rpm0-1")
            effects.dispose("prop_rpm0-2")
            effects.dispose("prop_rpm1-1")
            effects.dispose("prop_rpm1-2")
            return

        frequency = float(rpm) / 60

        # frequency = 20
        median_modulation = 2
        modulation_pos = 2
        modulation_neg = 1
        frequency2 = frequency + median_modulation
        precision = 2

        r1_modulation = utils.sine_point_in_time(3, 10000)
        r2_modulation = utils.sine_point_in_time(3, 17500, phase_offset_deg=45)

        if frequency > 0:
            force_limit = max(self.engine_rumble_highrpm_intensity, self.engine_rumble_lowrpm_intensity)
            dynamic_rumble_intensity = utils.clamp(self._calc_engine_intensity(rpm), 0, force_limit)
            logging.debug(f"Current Engine Rumble Intensity = {dynamic_rumble_intensity}")

            effects["prop_rpm0-1"].periodic(frequency, dynamic_rumble_intensity, 0).start()  # vib on X axis
            effects["prop_rpm0-2"].periodic(frequency + r1_modulation, dynamic_rumble_intensity, 0).start()  # vib on X
            effects["prop_rpm1-1"].periodic(frequency2, dynamic_rumble_intensity, 90).start()  # vib on Y axis
            effects["prop_rpm1-2"].periodic(frequency2 + r2_modulation, dynamic_rumble_intensity, 90).start()  # vib on Y
        else:
            effects.dispose("prop_rpm0-1")
            effects.dispose("prop_rpm0-2")
            effects.dispose("prop_rpm1-1")
            effects.dispose("prop_rpm1-2")

    def _calc_engine_intensity(self, rpm) -> float:
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
    def _update_ab_effect(self, telem_data):
        if not self.afterburner_effect_intensity or not self.afterburner_effect_enabled:
            effects.dispose("ab_rumble_1_1")
            effects.dispose("ab_rumble_2_1")
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

    def _update_jet_engine_rumble(self, telem_data):
        if not self.engine_jet_rumble_enabled or not self.jet_engine_rumble_intensity > 0:
            effects.dispose("je_rumble_1_1")
            effects.dispose("je_rumble_1_2")
            effects.dispose("je_rumble_2_1")
            effects.dispose("je_rumble_2_2")
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
            effects.dispose("je_rumble_1_1")
            effects.dispose("je_rumble_1_2")
            effects.dispose("je_rumble_2_1")
            effects.dispose("je_rumble_2_2")
            return
        
        r1_modulation = utils.sine_point_in_time(2, 30000)
        r2_modulation = utils.sine_point_in_time(2, 22500, phase_offset_deg=0)
        intensity = self.jet_engine_rumble_intensity * (jet_eng_rpm / 100)
        intensity = utils.clamp(intensity, 0, 1)
        rt_freq = round(frequency + (10 * (jet_eng_rpm / 100)), 4)
        rt_freq2 = round(rt_freq + median_modulation, 4)
        effects["je_rumble_1_1"].periodic(rt_freq, intensity, 0, effect_index).start()
        effects["je_rumble_1_2"].periodic(rt_freq + r1_modulation, intensity, 0, effect_index).start()
        effects["je_rumble_2_1"].periodic(rt_freq2, intensity, 90, effect_index, phase=phase_offset).start()
        effects["je_rumble_2_2"].periodic(rt_freq2 + r2_modulation, intensity, 90, effect_index, phase=phase_offset+30).start()
        logging.debug(f"JE-M1={r1_modulation}, F1-1={rt_freq}, F1-2={round(rt_freq + r1_modulation,4)} | JE-M2 = {r2_modulation}, F2-1={rt_freq2}, F2-2={round(rt_freq2 + r2_modulation, 4)} ")


    ########################################
    ######                            ######
    ######     Helicopter Effects     ######
    ######                            ######
    ########################################

    def _calc_etl_effect(self, telem_data, blade_ct=None):
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
            effects.dispose("etlX")
            effects.dispose("etlY")
            effects.dispose("overspeedX")
            effects.dispose("overspeedY")
            return
        if blade_ct is None:
            blade_ct = 2
            rotor = 250
        if self._sim_is_dcs() and "AH-64" in mod:
            rotor = 245  # Apache does not have exportable data related to Rotor RPM

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
            effects.dispose("etlX")
            effects.dispose("etlY")

        if tas >= self.overspeed_shake_start and self.overspeed_effect_enable:
            shake = self.overspeed_shake_intensity * utils.non_linear_scaling(tas, self.overspeed_shake_start, self.overspeed_shake_start + 15, curvature=.7)
            shake = utils.clamp(shake, 0.0, 1.0)
            effects["overspeedY"].periodic(self.overspeed_shake_frequency, shake, 0).start()
            effects["overspeedX"].periodic(self.overspeed_shake_frequency + 4, shake, 90).start()
            logging.debug(f"Overspeed shake (freq = {self.etl_shake_frequency}, intens= {shake}) ")
        else:
            effects.dispose("overspeedX")
            effects.dispose("overspeedY")

    def _update_vrs_effect(self, telem_data):
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
            effects.dispose("vrs_buffet")
            effects.dispose("vrs_buffet2")
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
            effects.dispose("vrs_buffet")
            effects.dispose("vrs_buffet2")

    def _update_heli_engine_rumble(self, telem_data, blade_ct=None):
        if not self.engine_rotor_rumble_enabled or not self.heli_engine_rumble_intensity:
            effects.dispose("rotor_rpm0-1")
            effects.dispose("rotor_rpm1-1")
            return
        if self._sim_is_xplane():
            rrpm = telem_data.get("PropRPM", 0)
            if isinstance(rrpm, list):
                rrpm = rrpm[0]
        else:
            rrpm = telem_data.get("RotorRPM", 0)
            if isinstance(rrpm, list):
                rrpm = max(rrpm)
        mod = telem_data.get("N")
        tas = telem_data.get("TAS", 0)
        eng_rpm = telem_data.get("EngRPM", 0)
        if isinstance(eng_rpm, list):
            eng_rpm = max(eng_rpm)

        # rotor = telem_data.get("RotorRPM")


        if blade_ct is None:
            blade_ct = 2
            rrpm = 250

        if self._sim_is_dcs() and "AH-64" in mod:
            rrpm = 245  # Apache does not have exportable data related to Rotor RPM

        if rrpm < 5:
            effects.dispose("rotor_rpm0-1")
            effects.dispose("rotor_rpm1-1")
            return

        logging.debug(f"Engine Rumble: Blade_Ct={blade_ct}, RPM={rrpm}")
        frequency = float(rrpm) / 45 * blade_ct

        median_modulation = 2
        frequency2 = frequency + median_modulation
        if frequency > 0 and eng_rpm > 0:
            logging.debug(f"Current Heli Engine Rumble Intensity = {self.heli_engine_rumble_intensity}")
            effects["rotor_rpm0-1"].periodic(frequency, self.heli_engine_rumble_intensity * .5, 0).start()  # vib on X axis
            effects["rotor_rpm1-1"].periodic(frequency2, self.heli_engine_rumble_intensity * .5, 90).start()  # vib on Y axis
        else:
            effects.dispose("rotor_rpm0-1")
            effects.dispose("rotor_rpm1-1")

    def _override_pedal_spring(self, telem_data):
        if not self.is_pedals(): return

        input_data = HapticEffect.device.get_input()
        phys_x, phys_y = input_data.axisXY()
        ## 0=DCS Default
        ## 1=spring disabled
        ## 2=static spring enabled using "pedal_spring_gain" spring setting
        ## 3=dynamic spring enabled.  Based on "pedal_spring_gain"
        if self.pedal_spring_mode == 0:
            return
        elif self.pedal_spring_mode == 'No Spring':
            self.spring_x.positiveCoefficient = 0
            self.spring_x.negativeCoefficient = 0

        elif self.pedal_spring_mode == 'Static Spring':
            spring_coeff = round(utils.clamp((self.pedal_spring_gain *4096), 0, 4096))
            self.spring_x.positiveCoefficient = self.spring_x.negativeCoefficient = spring_coeff

            if self.pedal_trimming_enabled:
                self._update_pedal_trim(telem_data)

        elif self.pedal_spring_mode == 'Dynamic Spring':
            tas = telem_data.get("TAS", 0)
            # ac_perf = self.get_aircraft_perf(telem_data)
            # if self.aircraft_vs_speed:
                #If user has added the speeds to their config, use that value
            vs = self.aircraft_vs_speed
            # else:
                #Otherwise, use the value from the internal table
                # vs = ac_perf['Vs']

            # if self.aircraft_vne_speed:
            vne = self.aircraft_vne_speed
            # else:
            #     vne = ac_perf['Vne']

            if vs > vne:
                #log error if vs speed is configured greater than vne speed and exit
                logging.error(f"Dynamic pedal forces error: Vs speed ({vs}) is configured with a larger value than Vne ({vne}) - Invalid configuration")
                telem_data['error'] = 1

            vs_coeff = utils.clamp(round(self.aircraft_vs_gain*4096), 0, 4096)
            vne_coeff = utils.clamp(round(self.aircraft_vne_gain*4096), 0, 4096)
            spr_coeff = utils.scale(tas, (vs, vne), (vs_coeff, vne_coeff))
            spr_coeff = round(spr_coeff * self.pedal_spring_gain)
            spr_coeff = utils.clamp(spr_coeff, 0, 4096)
            # print(f"coeff={spr_coeff}")
            self.spring_x.positiveCoefficient = spr_coeff
            self.spring_x.negativeCoefficient = spr_coeff
            if self.pedal_trimming_enabled:
                self._update_pedal_trim(telem_data)
            # return
        spring = effects["pedal_spring"].spring()
        damper_coeff = round(utils.clamp((self.pedal_dampening_gain * 4096), 0, 4096))
        # self.damper = effects["pedal_damper"].damper(coef_x=damper_coeff).start()

        spring.effect.setCondition(self.spring_x)
        spring.start(override=True)

    def on_event(self):
        pass

    def on_timeout(self):  # override me
        logging.info("Telemetry Timeout, stopping effects")
        # effects.foreach(lambda e: e.stop())
        for key, effect in effects.dict.items():
            if self.keep_forces_on_pause:
                if key in ["damper", "inertia", "friction", "spring"]:
                    continue
            effect.stop()

    def on_telemetry(self, telem_data): 
        pass
