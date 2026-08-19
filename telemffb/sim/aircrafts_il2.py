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


# 
# This file is part of the TelemFFB distribution (https://github.com/walmis/TelemFFB).
# Copyright (c) 2023 Valmantas Palikša.
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
 
import math
from random import randint
import time
from typing import List, Dict, override
from telemffb.hw.ffb_rhino import HapticEffect, FFBReport_SetCondition
import telemffb.utils as utils
import telemffb.globals as G
import logging
import random
from .aircraft_base import AircraftBase
import json
from telemffb.SettingsManager import GEffectModeEnum, SpringModeEnum
from telemffb.util import conversions as conv
from telemffb.sim.BaseTelemetryData import BaseTelemetryData
perftracker = utils.PerformanceTracker()

# unit conversions (to m/s)
knots = conv.kt2ms
kmh = conv.kmh2ms
deg = math.pi/180
EFFECT_SQUARE = 3
EFFECT_SINE = 4
EFFECT_TRIANGLE = 5
EFFECT_SAWTOOTHUP = 6
EFFECT_SAWTOOTHDOWN = 7
# by accessing effects dict directly new effects will be automatically allocated
# example: effects["myUniqueName"]
# effects : Dict[str, HapticEffect] = utils.Dispenser(HapticEffect)

dbg_en = 0
dbg_lvl = 2
def dbg(level, *args, **kwargs):
    if dbg_en and level >= dbg_lvl:
        print(*args, **kwargs)

class Aircraft(AircraftBase):
    """Base class for Aircraft based FFB"""
    ####
    #### Beta effects - set to 1 to enable
    deceleration_effect_enable = 0
    deceleration_effect_enable_areyoureallysure = 0
    deceleration_max_force = 0.5
    ###
    buffeting_intensity : float = 0.2               # peak AoA buffeting intensity  0 to disable
    buffet_aoa : float          = 10.0              # AoA when buffeting starts
    stall_aoa : float           = 15.0              # Stall AoA
    wind_effect_enabled : int = 0


    runway_rumble_intensity : float = 1.0           # peak runway intensity, 0 to disable
    runway_rumble_enabled: bool = False
    il2_runway_rumble_intensity : float = 1.0           # peak runway intensity, 0 to disable

    gun_vibration_intensity : float = 0.12          # peak gunfire vibration intensity, 0 to disable
    cm_vibration_intensity : float = 0.12           # peak countermeasure release vibration intensity, 0 to disable
    weapon_release_intensity : float = 0.12         # peak weapon release vibration intensity, 0 to disable
    il2_weapon_release_intensity : float = 0.12         # peak weapon release vibration intensity, 0 to disable
    il2_bomb_release_intensity : float = 0.12         # peak weapon release vibration intensity, 0 to disable
    il2_rocket_release_intensity : float = 0.12         # peak weapon release vibration intensity, 0 to disable
    weapon_effect_direction: int = 45               # Affects the direction of force applied for gun/cm/weapon release effect, Set to -1 for random direction
    
    speedbrake_motion_intensity : float = 0.12      # peak vibration intensity when speed brake is moving, 0 to disable
    speedbrake_buffet_intensity : float = 0.15      # peak buffeting intensity when speed brake deployed,  0 to disable

    spoiler_motion_intensity: float = 0.0  # peak vibration intensity when spoilers is moving, 0 to disable
    spoiler_buffet_intensity: float = 0.15  # peak buffeting intensity when spoilers deployed,  0 to disable

    gear_motion_effect_enabled: bool = True
    gear_motion_intensity : float = 0.12      # peak vibration intensity when gear is moving, 0 to disable

    flaps_motion_intensity : float = 0.12      # peak vibration intensity when flaps are moving, 0 to disable

    jet_engine_rumble_intensity = 0.12      # peak intensity for jet engine rumble effect
    jet_engine_rumble_freq = 45             # base frequency for jet engine rumble effect (Hz)



    gun_is_firing = 0
    damage_effect_enabled: bool = False
    damage_effect_intensity: float = 0
    il2_shake_master = 0
    il2_enable_weapons = 0
    il2_enable_runway_rumble = 0  # not yet implemented
    il2_enable_buffet = 0  # not yet impelemnted
    il2_buffeting_factor: float  = 1.0
    il2_dynamic_gunfire_mode = False

    il2_prop_eng_shake_enabled: bool = False
    il2_prop_eng_shake_factor: float = 1.0

    il2_jet_eng_shake_enabled: bool = False
    il2_jet_eng_shake_factor: float = 1.0
    #stop_state = False

    def __init__(self, name : str, **kwargs):
        super().__init__(name, **kwargs)
        self.gun_is_firing = 0
        self.gun_is_firing_dict = {}
        #clear any existing effects
        self.spring = self.effects["spring"].spring()
        # self.damper = effects["damper"].damper()
        self.spring_x = FFBReport_SetCondition(parameterBlockOffset=0)
        self.spring_y = FFBReport_SetCondition(parameterBlockOffset=1)
        for e in self.effects.values(): e.destroy()
        self.effects.clear()

        self.sprin_mode = SpringModeEnum.NONE.name

        # self.spring = HapticEffect().spring()
        # self.spring_x = FFBReport_SetCondition(parameterBlockOffset=0)
        # self.spring_y = FFBReport_SetCondition(parameterBlockOffset=1)

    @staticmethod
    def _il2_engine_value(val) -> float:
        """Reduce an IL-2 per-engine indicator (ENG_RPM, ENG_SHAKE_FRQ, ...) to one float.

        The sim sends one value per engine slot, but the slot count is not stable across
        sim builds: 4.003 sent Values_count=1 for the single-engine F-51D, a later build
        sends 3 zero-padded slots for the same aircraft.  The telemetry string pipeline
        then collapses a one-element array back to a bare scalar (TelemManager only builds
        a list when len(values) > 1), and to_number() passes through the original string
        when it can't parse it.  So this can legitimately arrive as a float, a list, or ''
        before the first indicator has been received.  Normalize every shape, and treat
        anything non-finite or unparseable as 0 so the caller's zero-check catches it.
        """
        def num(x) -> float:
            try:
                x = float(x)
            except (TypeError, ValueError):
                return 0.0
            return x if math.isfinite(x) else 0.0

        if isinstance(val, (list, tuple)):
            # Coerce per element, not after max():  a NaN slot round-trips as the
            # string 'nan', which would make this a mixed str/float list and blow
            # up the comparison inside max().
            return max((num(x) for x in val), default=0.0)
        return num(val)

    def il2_update_engine_shake(self, telem_data: BaseTelemetryData):
        """Alternative engine shake using harmonic ratios, amplitude taper, and direction spread.

        Prop: fundamental + 2nd harmonic (2x) + slow sine-modulated detuning.
        Jet:  fundamental + 1.5x partial + slower, smaller modulation.
        Upper harmonic pair plays at 65% amplitude. Direction spread avoids axis-lock.
        Modulation period scales with frequency so variation feels proportional to RPM.
        """
        factor = 0
        freq_offset = 0

        if telem_data.AircraftClass == 'PropellerAircraft':
            factor = self.il2_prop_eng_shake_factor
            if not self.il2_prop_eng_shake_enabled:
                self.effects.dispose("il2_eng_shk1", "il2_eng_shk2", "il2_eng_shk3", "il2_eng_shk4")
                return
        elif telem_data.AircraftClass == 'JetAircraft':
            factor = self.il2_jet_eng_shake_factor
            freq_offset = 30
            if not self.il2_jet_eng_shake_enabled:
                self.effects.dispose("il2_jet_shk1", "il2_jet_shk2")
                return
        else:
            return

        frequency = self._il2_engine_value(telem_data.get('EngineShakeFrequency'))
        amplitude = self._il2_engine_value(telem_data.get('EngineShakeAmplitude'))

        if not frequency or not amplitude:
            return

        frequency = float(frequency) + freq_offset
        amplitude = float(amplitude) * factor * 3

        if frequency <= 0:
            self.effects.dispose("il2_eng_shk1", "il2_eng_shk2", "il2_eng_shk3", "il2_eng_shk4", "il2_jet_shk1", "il2_jet_shk2")
            return

        # Harmonic: prop uses 2nd harmonic (2x), jet uses 3rd partial (1.5x — less harsh)
        if telem_data.AircraftClass == 'PropellerAircraft':
            frequency2 = frequency * 2.0
        elif telem_data.AircraftClass == 'JetAircraft':
            frequency2 = frequency * 1.5

        # Modulation period scales with frequency so drift feels proportional at all RPMs
        mod_period1 = max(5000, int(1000 / frequency * 80))
        mod_period2 = int(mod_period1 * 1.75)

        r1_mod = utils.sine_point_in_time(3, mod_period1)
        r2_mod = utils.sine_point_in_time(2, mod_period2, phase_offset_deg=45)

        # Upper harmonic plays quieter; small direction spread avoids axis-locked feel
        amp2 = amplitude * 0.65

        if telem_data.AircraftClass == 'PropellerAircraft':
            self.effects["il2_eng_shk1"].periodic(frequency,            amplitude, 0  ).start()
            self.effects["il2_eng_shk2"].periodic(frequency + r1_mod,   amplitude, 10 ).start()
            self.effects["il2_eng_shk3"].periodic(frequency2,           amp2,      80 ).start()
            self.effects["il2_eng_shk4"].periodic(frequency2 + r2_mod,  amp2,      100).start()
        elif telem_data.AircraftClass == 'JetAircraft':
            self.effects["il2_jet_shk1"].periodic(frequency  + r1_mod,  amplitude, 0,  phase=0  ).start()
            self.effects["il2_jet_shk2"].periodic(frequency2 + r2_mod,  amp2,      90, phase=120).start()

    @override
    def ac_update_cm_weapons(self, telem):
        if not self.il2_shake_master: return
        if not self.il2_enable_weapons: return

        ## IL2 does not deliver telemetry in the same way as DCS.  As a result, modified/different effects logic is required
        canon_rof = 600
        canon_hz = canon_rof/60
        bombs = telem.get("Bombs")
        gun = telem.get("Gun")
        rockets = telem.get("Rockets")
        direction = 90 if self.is_pedals() else 0
        if self.anything_has_changed("Bombs", bombs):
            self.effects["il2_bombs"].periodic(10, self.il2_bomb_release_intensity, direction,effect_type=EFFECT_SAWTOOTHUP, duration=80).start(force=True)
        elif not self.anything_has_changed("Bombs", bombs, delta_ms=160):
            self.effects["il2_bombs"].stop()

        if self.il2_dynamic_gunfire_mode:
            gunfire_dict = json.loads(telem.get('GunFireData', '{}'))

            for weapon, rounds in json.loads(telem.get('GunFireData', '{}')).items():
                is_firing = self.gun_is_firing_dict.get(weapon, False)
                weapon_l = [float(x.strip()) for x in weapon.split(",")]
                if self.anything_has_changed(weapon, rounds, delta_ms=100) and not is_firing:
                    rate, factor = self.gun_effect_from_mv(weapon_l[0], weapon_l[1])
                    print(f"rate:{int(rate)}, rpm:{int(rate)*60}, factor:{round(factor, 3)}")
                    self.effects[f"il2_gunfire_{weapon}"].periodic(int(rate), utils.clamp(self.il2_weapon_release_intensity * factor, 0, 1), direction, effect_type=EFFECT_SAWTOOTHUP).start()
                    self.gun_is_firing_dict[weapon] = True
                elif not self.anything_has_changed(weapon, rounds, delta_ms=100):
                    self.effects.dispose(f"il2_gunfire_{weapon}")
                    self.gun_is_firing_dict[weapon] = False

        else:
            if self.anything_has_changed("Gun", gun) and not self.gun_is_firing:
                self.effects["il2_gunfire"].periodic(canon_hz, self.il2_weapon_release_intensity, direction, effect_type=EFFECT_SQUARE).start(force=True)
                self.gun_is_firing = 1
                logging.debug(f"Gunfire={self.il2_weapon_release_intensity}")
            elif not self.anything_has_changed("Gun", gun, delta_ms=100):
                # effects["gunfire"].stop()
                self.effects.dispose("il2_gunfire")
                self.gun_is_firing = 0

        if self.anything_has_changed("Rockets", rockets):
            self.effects["il2_rockets"].periodic(50, self.il2_rocket_release_intensity, direction, effect_type=EFFECT_SQUARE, duration=80).start(force=True)
        if not self.anything_has_changed("Rockets", rockets, delta_ms=160):
            self.effects["il2_rockets"].stop()

    @override
    def ac_update_runway_rumble(self, telem_data: BaseTelemetryData):
        if not self.il2_shake_master: return
        if not self.il2_enable_runway_rumble: return

        if telem_data.TAS > 1.0 and telem_data.AGL < 10.0 and utils.average(telem_data.GearPos) == 1:
            self.runway_rumble_intensity = self.il2_runway_rumble_intensity
            super().ac_update_runway_rumble(telem_data)
        else:
            self.runway_rumble_intensity = 0
            self.effects.dispose("runway0", "runway1")

    @override
    def ac_update_buffeting(self, telem_data: dict):
        if not self.il2_shake_master: return
        if not self.il2_enable_buffet: return

        direction = 90 if self.is_pedals() else 0
        freq = telem_data.BuffetFrequency or 0
        amp = utils.clamp((telem_data.BuffetAmplitude or 0) * self.il2_buffeting_factor, 0.0, 1.0)
        amp2 = utils.clamp(amp * 1.4, 0, 1)
        if amp:
            self.effects["il2_buffet"].periodic(freq, amp, direction, effect_type=EFFECT_SINE).start()
            self.effects["il2_buffet2"].periodic(freq * 1.5, amp2, direction + 180, effect_type=EFFECT_SINE, phase=90).start()

        else:
            self.effects.dispose("il2_buffet", "il2_buffet2")

    def il2_update_damage(self, telem_data: BaseTelemetryData):
        if not self.damage_effect_enabled or not self.damage_effect_intensity:
            self.effects.dispose("hit", "damage")
            return

        hit = telem_data.Hits
        damage = telem_data.Damage
        hit_freq = 5
        hit_amp = utils.clamp(self.damage_effect_intensity, 0.0, 1.0)
        damage_freq = 10
        damage_amp = utils.clamp(self.damage_effect_intensity, 0.0, 1.0)

        if self.anything_has_changed("hit", hit):
            self.effects["hit"].periodic(hit_freq, hit_amp, utils.RandomDirectionModulator,effect_type=EFFECT_SQUARE, duration=30).start()
        elif not self.anything_has_changed("hit", hit, delta_ms=120):
            self.effects.dispose("hit")
        if self.anything_has_changed("damage", damage):
            self.effects["damage"].periodic(damage_freq, damage_amp, utils.RandomDirectionModulator, effect_type=EFFECT_SQUARE, duration=30).start()
        elif not self.anything_has_changed("damage", damage, delta_ms=120):
            self.effects.dispose("damage")

    @override
    def on_telemetry(self, telem_data: BaseTelemetryData):
        ## Generic Aircraft Telemetry Handler
        """when telemetry frame is received, aircraft class receives data in dict format

        :param new_data: New telemetry data
        :type new_data: dict
        """
        # if telem_data.SimPaused or telem_data.MPMenu or not telem_data.Focus:
        #     if not self.stop_state:
        #         self.on_timeout()
        #     self.stop_state = True
        #     G.telem_manager.telemetryTimeout.emit(True)
        #     return
        #
        # self.stop_state = False

        if telem_data.AircraftClass == "unknown":
            telem_data.AircraftClass = "GenericAircraft" #inject aircraft class into telemetry
        self._telem_data = telem_data

        if telem_data.N is None:
            return

        # call Base class handler
        super().on_telemetry(telem_data)
        # self._update_focus_loss(telem_data)
        self.il2_override_spring()
        self.il2_ffb_spring()
        self.il2_ffb_forces()
        if self.damage_effect_intensity > 0:
            self.il2_update_damage(telem_data)


    @override
    def on_event(self, event, *args):
        logging.info(f"on_event: {event}")
        if event == "Stop":
            self.effects.clear()

    def gun_effect_from_mv(self, m_kg: float, v_mps: float):
        """
        Returns:
          sps  : shots per second (Hz) for your haptic timer
          rfac : recoil multiplier (tight range, ~0.8–2.0)

        Method:
          1) p_eff = (1 + K_GAS) * m * v  (impulse -> band)
          2) sps within band = linear interp by p_eff, then
             apply a small velocity bias so light/fast rounds are a bit faster,
             heavy/slow rounds a bit slower (removes collisions).
          3) rfac = tanh-compressed function of p_eff (unchanged).
        """
        # --- constants ---
        K_GAS = 1.5  # propellant gas momentum fraction
        P_REF = 120.0  # N·s (≈ aircraft .50 cal baseline)
        SPAN, S = 0.6, 0.9
        RF_MIN, RF_MAX = 0.8, 2.0

        # bands: (p_eff_lo, p_eff_hi, sps_lo, sps_hi, (v_min, v_max) for biasing)
        bands = [
            (0.0, 60.0, 20.0, 25.0, (700.0, 900.0)),  # 7.62-ish aircraft MG (rare)
            (60.0, 140.0, 18.0, 22.0, (750.0, 900.0)),  # .50 / 12.7–13 mm
            (140.0, 300.0, 10.0, 16.0, (600.0, 900.0)),  # 20 mm class
            (300.0, 600.0, 9.0, 15.0, (700.0, 900.0)),  # 23–30 mm light/high-perf
            (600.0, 1e9, 5.0, 12.0, (500.0, 800.0)),  # 30–40 mm heavy/low-vel
        ]

        # --- effective impulse ---
        p_eff = (1.0 + K_GAS) * m_kg * v_mps

        # --- sps from band + velocity bias ---
        for lo, hi, sps_lo, sps_hi, (vmin, vmax) in bands:
            if p_eff <= hi:
                # base interpolation by impulse (higher p -> slower)
                t = 0.0 if hi == lo else (max(lo, min(p_eff, hi)) - lo) / (hi - lo)  # 0..1
                # velocity normalization inside a plausible window per band
                v_clamped = max(min(v_mps, vmax), vmin)
                v_norm = (v_clamped - vmin) / (vmax - vmin) if vmax > vmin else 0.5
                # bias: higher v -> slightly faster (reduce t), lower v -> slower (increase t)
                W_V = 0.25  # <= main tuning knob; 0.15–0.30 is a good range
                t_biased = min(1.0, max(0.0, t - W_V * (v_norm - 0.5)))
                sps = sps_lo + (1.0 - t_biased) * (sps_hi - sps_lo)
                break

        # --- recoil factor (same as before) ---
        ratio = max(1e-12, p_eff / P_REF)
        rfac = 1.0 + SPAN * math.tanh(S * math.log(ratio))
        rfac = max(RF_MIN, min(RF_MAX, rfac))

        return sps, rfac

    def il2_override_spring(self):
        if not self.is_joystick(): return
        if not self.spring_mode_is(SpringModeEnum.CUSTOM):
            # If feature disabled, ensure spring is stopped and abort
            self.effects['il2_spr_override'].stop()
            return

        spring = self.effects['il2_spr_override'].spring()

        dt = perftracker.get_time_delta('override_spring_perf')
        self.telem_data._ovrd_spr_dt = dt

        if self.override_spring_ft_enabled:
            input_data = self._get_device_report()
            x, y = self._get_device_axes()
            current_buttons = input_data.getPressedButtons() if input_data is not None else []
            # print(f"BUTTONS:>{current_buttons}<")
            # decide what to do depending on which button is pressed
            # if self.override_spring_trim_release and self.override_spring_trim_release in current_buttons:
            #     # use spring force as dampening.  Configured damper value applied as spring gain.  cpO will follow stick
            #     # as it is moved while spring force is enabled.
            #     # return from method so default spring gains do not get applied at the end of the method
            #     gain = int(self.override_spring_tr_damper * 4096)
            #     self.spring_x.set_coefficient(gain)
            #     self.spring_y.set_coefficient(gain)
            #
            #     self.override_spring_cp0_x = round(x * 4096)
            #     self.spring_x.set_offset(self.override_spring_cp0_x)
            #
            #     self.override_spring_cp0_y = round(y * 4096)
            #     self.spring_y.set_offset(self.override_spring_cp0_y)
            #     spring.setCondition(self.spring_x)
            #     spring.setCondition(self.spring_y)
            #     spring.start(override=True)
            #     return

            # elif self.override_spring_trim_reset and self.override_spring_trim_reset in current_buttons:
            #     # if trim reset button pressed, set offsets back to 0
            #     # print("TRIM RESET")
            #     self.spring_x.cpOffset = self.override_spring_cp0_x = 0
            #     self.spring_y.cpOffset = self.override_spring_cp0_y = 0
            #     spring.setCondition(self.spring_x)
            #     spring.setCondition(self.spring_y)

            # calculate step size based on configured rate and delta time
            trim_step_size = self.override_spring_trim_rate * dt

            self.telem_data._ovrd_spr_step = trim_step_size

            # evaluate UP or DOWN and then LEFT or RIGHT trims.  Allows movement on both axes simultaneously but not
            # accidental confliction of trying to move both directions on a single axis due to bad hat bindings
            if self.override_spring_trim_down and self.override_spring_trim_down in current_buttons:
                # shift offset based on previously calculated step size.  Ensure value does not exceed limits
                # print("TRIM DOWN")
                if self.override_spring_cp0_y - trim_step_size < -4096:
                    self.override_spring_cp0_y = -4096
                else:
                    self.override_spring_cp0_y -= trim_step_size
                self.spring_y.cpOffset = round(self.override_spring_cp0_y)
            elif self.override_spring_trim_up and self.override_spring_trim_up in current_buttons:
                # shift offset based on previously calculated step size.  Ensure value does not exceed limits
                # print("TRIM UP")
                if self.override_spring_cp0_y + trim_step_size > 4096:
                    self.override_spring_cp0_y = 4096
                else:
                    self.override_spring_cp0_y += trim_step_size
                self.spring_y.cpOffset = round(self.override_spring_cp0_y)

            if self.override_spring_trim_left and self.override_spring_trim_left in current_buttons:
                # shift offset based on previously calculated step size.  Ensure value does not exceed limits
                # print("TRIM LEFT")
                if self.override_spring_cp0_x - trim_step_size < -4096:
                    self.override_spring_cp0_x = -4096
                else:
                    self.override_spring_cp0_x -= trim_step_size
                self.spring_x.cpOffset = round(self.override_spring_cp0_x)
            elif self.override_spring_trim_right and self.override_spring_trim_right in current_buttons:
                # shift offset based on previously calculated step size.  Ensure value does not exceed limits
                # print("TRIM RIGHT")
                if self.override_spring_cp0_x + trim_step_size > 4096:
                    self.override_spring_cp0_x = 4096
                else:
                    self.override_spring_cp0_x += trim_step_size
                self.spring_x.cpOffset = round(self.override_spring_cp0_x)

        self.telem_data._ovrd_spr_trim_pos = [round(self.override_spring_cp0_x), round(self.override_spring_cp0_y)]

        # If trim release is not pressed, set spring gain based on user setting and start spring override
        self.spring_x.set_coefficient(self.override_spring_gain)
        self.spring_y.set_coefficient(self.override_spring_gain)

        spring.setCondition(self.spring_x)
        spring.setCondition(self.spring_y)
        # ensure spring is started with override = true
        spring.start(override=True)


    def il2_ffb_spring(self, force=False):
        """Apply a spring effect driven directly by IL-2 Korea FFB telemetry records.

        For each Spring-type record addressed to this device, axis 0 drives spring_x and
        axis 1 drives spring_y.  The record's `pos` field (−1..1) sets the spring centre-point
        offset and the `force` field (0..1) sets the spring coefficient.  Both are passed
        directly to set_offset / set_coefficient which handle the ×4096 scaling internally.
        """
        from telemffb.telem.IL2Manager import ForceType

        if not self.spring_mode_is(SpringModeEnum.TELEM) and not self.spring_mode_is(SpringModeEnum.ADVANCED):
            self.effects['il2_ffb_spring'].stop()
            return
        if self.spring_mode_is(SpringModeEnum.ADVANCED) and self.adv_spr_use_hardware_trim:
            self.effects['il2_ffb_spring'].stop()
            return

        raw = self.telem_data.FFBRecords
        records = json.loads(raw) if isinstance(raw, str) and raw else []
        spring_records = [r for r in records if r.get('type') == ForceType.Spring
                          and G.il2_ffb_device_ordinal is not None and r.get('dev') == G.il2_ffb_device_ordinal]

        if spring_records:
            self._last_ffb_spring_records = spring_records
        else:
            # IL-2 Korea sends pedal records every other tick — use last cached records
            spring_records = getattr(self, '_last_ffb_spring_records', [])

        if not spring_records:
            return

        spring = self.effects['il2_ffb_spring'].spring()
        for r in spring_records:
            axis = r.get('axis')
            if axis == 0:
                self.spring_x.set_offset(r['pos'])
                self.spring_x.set_coefficient(r['force'])
                spring.setCondition(self.spring_x)
                self.telem_data['FFB_X_Force'] = round(r['force'], 4)
                self.telem_data['FFB_X_Center'] = round(r['pos'], 4)
            elif axis == 1:
                self.spring_y.set_offset(r['pos'])
                self.spring_y.set_coefficient(r['force'])
                spring.setCondition(self.spring_y)
                self.telem_data['FFB_Y_Force'] = round(r['force'], 4)
                self.telem_data['FFB_Y_Center'] = round(r['pos'], 4)
        spring.start(override=True)

    def il2_ffb_forces(self):
        """Render IL-2 Korea Const/Damper FFB records on generic DirectInput
        devices.

        On VPforce hardware these records are already rendered by the game
        itself through its native DirectInput channel (TelemFFB rides the
        separate raw-HID channel, so both coexist) - re-rendering them here
        would double the forces.  A generic DirectInput device is held
        exclusively by TelemFFB, so the game's own channel cannot reach it
        and the records must be re-rendered from telemetry.

        DORMANT as of 2026-08: field testing showed IL-2 Korea exclusive-
        acquires every attached controller regardless of its force-feedback
        setting AND only emits ffbdevice records while its FFB is enabled -
        so on a DI device the game blocks TelemFFB's effects whenever it has
        focus, and disabling the game's FFB stops the records.  The IL-2 sim
        gate therefore remains fully closed for DI devices
        (SimTelemListener.is_enabled).  This renderer is kept tested and
        ready pending a game-side fix (non-exclusive acquisition when FFB is
        disabled, or FFB-off record export).

        Const and Damper records are treated as transient per-frame commands
        (no caching, unlike the spring records): a stale constant-force
        record must not keep pushing after the game stops commanding it.
        Records also carry 'amp'/'freq' fields whose semantics are still
        under observation; they are exposed in telemetry for discovery.
        """
        from telemffb.telem.IL2Manager import ForceType

        if not G.device_di_guid:
            return  # VPforce: the game renders these natively
        if G.il2_ffb_device_ordinal is None:
            return

        raw = self.telem_data.FFBRecords
        records = json.loads(raw) if isinstance(raw, str) and raw else []

        const_recs = {}
        damper_recs = {}
        for r in records:
            if r.get('dev') != G.il2_ffb_device_ordinal:
                continue
            if r.get('type') == ForceType.Const:
                const_recs[r.get('axis')] = r
            elif r.get('type') == ForceType.Damper:
                damper_recs[r.get('axis')] = r

        # --- constant force: combine per-axis commands into one vector ---
        fx = utils.clamp((const_recs.get(0) or {}).get('force', 0.0), -1.0, 1.0)
        fy = utils.clamp((const_recs.get(1) or {}).get('force', 0.0), -1.0, 1.0)
        magnitude = min(math.sqrt(fx * fx + fy * fy), 1.0)
        if magnitude >= 0.005:
            # measured DirectInput polar convention: 0 deg pushes +Y,
            # 90 -> -X, 180 -> -Y, 270 -> +X  =>  direction = atan2(-fx, fy)
            direction = math.degrees(math.atan2(-fx, fy)) % 360
            self.effects['il2_ffb_const'].constant(magnitude, direction).start()
            for axis_name, rec in (('X', const_recs.get(0)), ('Y', const_recs.get(1))):
                if rec:
                    self.telem_data[f'FFB_Const{axis_name}'] = [
                        round(rec.get('force', 0.0), 3),
                        round(rec.get('amp', 0.0), 3),
                        round(rec.get('freq', 0.0), 2)]
        else:
            self.effects['il2_ffb_const'].stop()

        # --- damper: per-axis coefficients ---
        dx = utils.clamp((damper_recs.get(0) or {}).get('force', 0.0), 0.0, 1.0)
        dy = utils.clamp((damper_recs.get(1) or {}).get('force', 0.0), 0.0, 1.0)
        if dx or dy:
            self.effects['il2_ffb_damper'].damper(int(dx * 4096), int(dy * 4096)).start()
            self.telem_data['FFB_Damper'] = [round(dx, 3), round(dy, 3)]
        else:
            self.effects['il2_ffb_damper'].stop()


class PropellerAircraft(Aircraft):
    """Generic Class for Prop/WW2 aircraft"""

    engine_max_rpm = 2700                           # Assume engine RPM of 2700 at 'EngRPM' = 1.00 for aircraft not exporting 'ActualRPM' in lua script
    max_aoa_cf_force : float = 0.2 # CF force sent to device at %stall_aoa

    # run on every telemetry frame
    @override
    def on_telemetry(self, telem_data: BaseTelemetryData):
        telem_data.AircraftClass = "PropellerAircraft"   #inject aircraft class into telemetry
        self.il2_update_engine_shake(telem_data)
        super().on_telemetry(telem_data)

class JetAircraft(Aircraft):
    """Generic Class for Jets"""

    # run on every telemetry frame
    @override
    def on_telemetry(self, telem_data: BaseTelemetryData):
        telem_data.AircraftClass = "JetAircraft"   #inject aircraft class into telemetry
        self.il2_update_engine_shake(telem_data)
        super().on_telemetry(telem_data)
