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
import math
from typing import override
from .Helicopter import Helicopter
import telemffb.utils as utils
from telemffb.SettingsManager import SpringModeEnum
from telemffb.hw.ffb_rhino import HapticEffect
from telemffb.util.conversions import kt2ms, ms2kt
from telemffb.utils import clamp, PerformanceTracker
from telemffb.sim.BaseTelemetryData import BaseTelemetryData


import logging
import time

perftracker = PerformanceTracker()

class XAW109Helicopter(Helicopter):
    afcs_step_size = 2
    collective_init = 0
    collective_ap_spring_gain = 1
    collective_dampening_gain = 0
    collective_spring_coeff_y = 0
    hands_on_deadzone = 0.1
    hands_off_deadzone = 0.05
    feet_on_deadzone = 0.05
    feet_off_deadzone = 0.03
    hands_on_active = 0
    hands_on_x_active = 0
    hands_on_y_active = 0
    feet_on_active = 0
    send_individual_hands_on = 0
    vrs_effect_enable: bool = True
    vrs_effect_intensity = 0
    afcs_motion_rate: int = 4
    # Schmitt enter threshold for the pedal AFCS follower (field-tuned
    # 2026-08: 0.15 tracks the pedal migration through cruise; 0.6
    # reproduces the certified sim's near-motionless pedals).
    afcs_threshold_value: float = 0.15

    def __init__(self, name, **kwargs):
        super().__init__(name, **kwargs)

        self.phys_x, self.phys_y = self._get_device_axes()
        self.cpO_y = round(self.phys_y * 4096)


    def on_telemetry(self, telem_data: BaseTelemetryData):
        super().on_telemetry(telem_data)


    def on_timeout(self):
        super().on_timeout()

    def msfs_update_heli_controls(self, telem_data: BaseTelemetryData):
        super().msfs_update_heli_controls(telem_data)
        ffb_type = telem_data.FFBType or "joystick"
        ap_active = telem_data.APMaster or 0

        if ffb_type == "joystick":
            phys_x, phys_y = self._get_device_axes()
            telem_data.act_target_roll = phys_x
            telem_data.act_target_pitch = phys_y
            x_rate = telem_data.AW109_aileron_trim_rate or 0
            y_rate = telem_data.AW109_elevator_trim_rate or 0

            # self.cpO_x += x_rate
            # self.cpO_y += y_rate
            self.afcsx_step_size = x_rate * self.afcs_motion_rate
            self.afcsy_step_size = y_rate * self.afcs_motion_rate

            self.cpO_x += self.afcsx_step_size
            self.cpO_y += self.afcsy_step_size


            self.spring_x.cpOffset = round(self.cpO_x)
            self.spring_y.cpOffset = round(self.cpO_y)
            self._spring_handle.setCondition(self.spring_x)
            self._spring_handle.setCondition(self.spring_y)

            self._spring_handle.start()



    def _update_cyclic_trim(self, telem_data: BaseTelemetryData):
        # Trimming is handled by the AFCS integration - override parent class function
        pass

        # ---- Pedal AFCS following: modeled parallel trim ----------------------
    # The aircraft's own yaw servo (AW109_rudder_trim_rate) only commands
    # pedal motion when |trim_zero + coarse + fine| exceeds the certified
    # thresholds (hi = 0.6) — in an instrumented 4.5-minute flight that
    # fired ONCE, which is the "pedals feel dead" known issue. A real 109SP
    # pilot's flight test (x-plane.org topic 318989) shows the pedals
    # visibly tracking the anti-torque requirement: ~2 in of left pedal in
    # a heavy hover, migrating to ~1 in of RIGHT pedal by 135 kt, and
    # responding to power changes with feet off. This renderer models that
    # parallel trim by proportionally unwinding the LOW-PASSED trim demand
    # (zero + coarse; 'fine' excluded per the developer's anti-oscillation
    # advice) through a Schmitt deadband, with the rate faded as IAS rises.
    # A previous proportional attempt oscillated in yaw above ~70 kt: the
    # low-pass, the hysteresis band, and the IAS fade each attack that loop
    # (filtering cuts gain at the oscillation frequency; hysteresis stops
    # boundary chatter; the fade tames the stiff-weathervane regime).
    # The plugin's own rudder_trim_rate commands (upper modes at the
    # certified thresholds) remain authoritative whenever nonzero.
    # User knobs (existing settings, no new ones): afcs_threshold_value =
    # the Schmitt ENTER threshold (0.6 reproduces certified deadness; the
    # developer blessed 0.4; lower = livelier pedals), afcs_motion_rate =
    # step scale, same semantics as the cyclic follow.
    PEDAL_AFCS_EXIT_RATIO = 0.4     # Schmitt exit = enter threshold x this
    PEDAL_AFCS_LPF_TAU = 1.5        # s; trim-demand low-pass
    PEDAL_AFCS_IAS_FADE_KT = (60.0, 100.0)   # fade band (real 109: feet on
    PEDAL_AFCS_IAS_GAIN = (1.0, 0.4)         #   pedals below ~60 kt anyway)
    PEDAL_AFCS_PROP_CAP = 1.5       # max proportional step, x afcs_motion_rate

    def msfs_update_pedals(self, telem_data: BaseTelemetryData):

        if telem_data.FFBType != 'pedals':
            return

        phys_x, phys_y = self._get_device_axes()
        telem_data.phys_x = phys_x
        telem_data.pedal_position = phys_x
        telem_data.IAS_kt = (telem_data.IAS or 0) * ms2kt

        self._spring_handle.name = "pedal_ap_spring"

        if not self.pedals_init:

            self.spring_x.negativeCoefficient = self.spring_x.positiveCoefficient = self.pedal_spring_coeff_x
            if telem_data.get("SimOnGround", 1):
                self.cpO_x = 0
            else:
                self.cpO_x = round(4096 * self.last_pedal_x)
            self.spring_x.positiveCoefficient = self.spring_x.negativeCoefficient = round(
                4096 * utils.clamp(self.pedal_spring_gain, 0, 1))

            self.spring_x.cpOffset = self.cpO_x
            self._spring_handle.setCondition(self.spring_x)
            self._spring_handle.start()
            if self.cpO_x / 4096 - 0.1 < phys_x < self.cpO_x / 4096 + 0.1:
                # dont start sending position until physical pedals have centered
                self.pedals_init = 1
            else:
                return

        pedal_ft_released = telem_data.get("AW109_ped_force_trim_release_pressed", 0)

        if pedal_ft_released:
            if self.pedal_ft_damper_enabled:
                force = int(self.pedal_ft_damper_force * 4096)
            else:
                force = 0
            self.cpO_x = round(4096 * utils.clamp(phys_x, -1, 1))
            self.spring_x.cpOffset = self.cpO_x
            self.spring_x.negativeCoefficient = self.spring_x.positiveCoefficient = force
            self._spring_handle.setCondition(self.spring_x)
            self._spring_handle.start()
            # Feet-on: the demand estimate is stale the moment the pilot
            # overrides; restart it clean on release.
            self._pedal_afcs_s = None
        else:
            rate_cmd = telem_data.get("AW109_rudder_trim_rate", 0) or 0

            # Trim demand: abs-of-sum semantics per the developer ("the sum
            # of values is the usable trim"); 'fine' deliberately excluded.
            s_raw = (telem_data.get("AW109_rud_trim_zero", 0) or 0) + \
                    (telem_data.get("AW109_rud_trim_coarse", 0) or 0)
            now = time.perf_counter()
            dt = utils.clamp(now - getattr(self, '_pedal_afcs_t', now), 0.0, 0.2)
            self._pedal_afcs_t = now
            s_prev = getattr(self, '_pedal_afcs_s', None)
            if s_prev is None:
                s = s_raw
            elif self.PEDAL_AFCS_LPF_TAU > 0:
                s = s_prev + (s_raw - s_prev) * utils.clamp(
                    dt / self.PEDAL_AFCS_LPF_TAU, 0.0, 1.0)
            else:
                s = s_raw
            self._pedal_afcs_s = s

            enter = self.afcs_threshold_value or 0.15
            exit_th = enter * self.PEDAL_AFCS_EXIT_RATIO
            active = getattr(self, '_pedal_afcs_active', False)
            if abs(s) > enter:
                active = True
            elif abs(s) < exit_th:
                active = False
            self._pedal_afcs_active = active

            step = 0.0
            if rate_cmd:
                # Authoritative upper-mode servo command: bang-bang +/-1,
                # scaled exactly like the cyclic follow.
                step = rate_cmd * self.afcs_motion_rate
            elif active:
                ias_kt = (telem_data.IAS or 0) * ms2kt
                ias_gain = utils.scale_clamp(
                    ias_kt, self.PEDAL_AFCS_IAS_FADE_KT, self.PEDAL_AFCS_IAS_GAIN)
                mag = utils.clamp(abs(s) / enter, 0.0, self.PEDAL_AFCS_PROP_CAP)
                step = math.copysign(
                    self.afcs_motion_rate * mag * ias_gain, s)

            self.cpO_x = utils.clamp(self.cpO_x + step, -4096, 4096)
            telem_data._telemffb_moving_rud = bool(step)
            telem_data._pedal_afcs_demand = s
            telem_data._pedal_afcs_active = active

            force = round(4096 * self.pedal_spring_gain)
            self.spring_x.cpOffset = round(self.cpO_x)
            self.spring_y.cpOffset = 0
            self.spring_y.negativeCoefficient = self.spring_y.positiveCoefficient = 0
            self.spring_x.negativeCoefficient = self.spring_x.positiveCoefficient = int(self.pedal_spring_gain * force)
            self._spring_handle.setCondition(self.spring_x)
            self._spring_handle.setCondition(self.spring_y)
            self._spring_handle.start()

        self.last_pedal_x = phys_x

    def msfs_update_collective(self, telem_data: BaseTelemetryData):
        if telem_data.FFBType != 'collective':
            return

        self._spring_handle.name = "collective_ap_spring"
        # self.damper = effects["collective_damper"].damper()

        collective_ft_released = telem_data.AW109_col_force_trim_release_pressed or 0

        collective_rate = telem_data.AW109_collective_trim_rate or 0

        collective_v_mode = telem_data.AW109_collective_mode or 0

        collective_afcs_pos = telem_data.AW109_collective_ratio or 0
        axis_afcs_pos = utils.scale_clamp(collective_afcs_pos, (0,1), (4096, -4096), return_int=True)

        phys_x, phys_y = self._get_device_axes()
        telem_data.phys_y = phys_y

        if not self.collective_init:

            self.spring_y.set_coefficient(self.collective_ap_spring_gain, True)

            if telem_data.get("SimOnGround", 1):
                # Sim is on ground - set init point to full down
                self.cpO_y = 4096
            elif self.last_collective_y is None:
                # Air start or new aircraft.  Use current physical position as init point
                self.cpO_y = round(4096 * phys_y)
            else:
                # set init point to last point before pause
                self.cpO_y = round(4096 * self.last_collective_y)

            self.spring_y.cpOffset = self.cpO_y

            self._spring_handle.setCondition(self.spring_y)

            # override: init spring needs exclusive authority to drive the
            # lever to the init point (matches HPG collective and the base
            # no-spring fork; a plain start leaves other effects fighting
            # the spring and the lever stalls short of the init gate)
            self._spring_handle.start(override=True)

            if self.cpO_y / 4096 - 0.1 < phys_y < self.cpO_y / 4096 + 0.1:
                # Check if phys y position is within %10 of init point
                # dont start sending position until physical stick has centered
                self.collective_init = 1
                logging.info("Collective Initialized")
            else:
                return
        # Only reach here if collective position is initialized
        self.last_collective_y = phys_y

        if collective_ft_released:

            self.cpO_y = round(4096 * utils.clamp(phys_y, -1, 1))
            # print(self.cpO_y)
            self.spring_y.cpOffset = self.cpO_y

            # self.damper.damper(coef_y=0).start()
            self.spring_y.negativeCoefficient = self.spring_y.positiveCoefficient = int(
                4096 * self.trim_release_spring_gain)

            self._spring_handle.setCondition(self.spring_y)
            self._spring_handle.start()
        else:
            if collective_v_mode:
                self.cpO_y = axis_afcs_pos
            # self.cpO_y -= self.afcsy_step_size
            self.spring_y.cpOffset = round(self.cpO_y)

            # self.damper.damper(coef_y=0).start()
            self.spring_y.negativeCoefficient = self.spring_y.positiveCoefficient = 4096
            self.spring_y.set_coefficient(self.collective_ap_spring_gain, True)
            self._spring_handle.setCondition(self.spring_y)
            self._spring_handle.start()