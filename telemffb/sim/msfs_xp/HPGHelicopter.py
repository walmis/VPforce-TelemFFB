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

from typing import override
from .Helicopter import Helicopter
import telemffb.utils as utils
from telemffb.SettingsManager import SpringModeEnum
from telemffb.hw.ffb_rhino import HapticEffect
from telemffb.util.conversions import kt2ms
from telemffb.utils import clamp, PerformanceTracker


import logging
import time
from telemffb.sim.BaseTelemetryData import BaseTelemetryData

perftracker = PerformanceTracker()

class HPGHelicopter(Helicopter):
    # user parameters 
    sema_yaw_max = 5
    afcs_step_size = 2
    collective_init = 0
    collective_ap_spring_gain = 1
    collective_dampening_gain = 0
    collective_spring_coeff_y = 0
    hands_on_deadzone = 0.1
    hands_off_deadzone = 0.02
    feet_on_deadzone = 0.05
    feet_off_deadzone = 0.03
    hands_on_active = 0
    hands_on_x_active = 0
    hands_on_y_active = 0
    feet_on_active = 0
    send_individual_hands_on = 0
    vrs_effect_enable: bool = True
    vrs_effect_intensity = 0
    afcs_followup_trim_rate = 100
    handson_force_mode = False
    hands_on_force_threshold = 0.03
    handsoff_force_duration = 500  # ms
    handson_force_debug = False
    # end of user parameters

    def __init__(self, name, **kwargs):
        super().__init__(name, **kwargs)

        input_data = HapticEffect.device.get_input()
        self.phys_x, self.phys_y = input_data.axisXY()
        self.cpO_y = round(self.phys_y * 4096)
        self.collective_spring_coeff_y = round(4096 * utils.clamp(self.collective_ap_spring_gain, 0, 1))
        self.hands_on_active = 0
        self.hands_on_x_active = 0
        self.hands_on_y_active = 0
        self.feet_on_active = 0
        self.followup_trim_accumulator = 0.0
        self.tracker_var = False
        self._last_hands_on_time_ms = 0
        self.pedals_init = 0

    @override
    def on_telemetry(self, telem_data: BaseTelemetryData):
        super().on_telemetry(telem_data)

        # self._update_vrs_effect(telem_data)
    @override
    def on_timeout(self):
        super().on_timeout()
        self.cyclic_spring_init = 0
        self.collective_init = 0
        self.pedals_init = 0

    def check_feet_on(self, percent):
        input_data = HapticEffect.device.get_input()
        phys_x, phys_y = input_data.axisXY()

        # Convert phys input to +/-4096
        phys_x = round(phys_x * 4096)

        ref_x = self.cpO_x

        # Calculate the threshold values based on the input percentage
        threshold = 4096 * percent

        # Calculate the deviation percentages in decimal form
        deviation_x = abs(phys_x - ref_x) / 4096

        # Check if either phys_x or phys_y exceeds the threshold
        x_exceeds_threshold = abs(phys_x - ref_x) > threshold

        result = {
            "result": x_exceeds_threshold,
            "deviation": deviation_x,
        }

        return result

    @override
    def msfs_update_heli_controls(self, telem_data: BaseTelemetryData):
        super().msfs_update_heli_controls(telem_data)
        ffb_type = telem_data.FFBType or "joystick"
        ap_active = telem_data.APMaster or 0
        if self.cyclic_spring_init:
            trim_reset = telem_data.hpgTrimRelease or 0
        else:
            trim_reset = False
        input_data = HapticEffect.device.get_input()
        force_trim_pressed = input_data.isButtonPressed(self.force_trim_button) if self.cyclic_spring_init else False
        if force_trim_pressed:
            self._simconnect.send_event_to_msfs("ROTOR_TRIM_RESET", 1)

        if ffb_type == "joystick":
            if not self.telemffb_controls_axes and not self.local_disable_axis_control:
                self.flag_error(
                    "Aircraft is configured as class HPGHelicopter.  For proper integration, TelemFFB must send axis position to MSFS.\n\nPlease enable 'telemffb_controls_axes' in your config and unbind the cyclic axes in MSFS settings")
                return
            sema_x = telem_data.hpgSEMAx or 0
            sema_y = telem_data.hpgSEMAy or 0

            sema_x_avg = self.smoother.get_rolling_average('s_sema_x', sema_x, window_ms=100)
            sema_y_avg = self.smoother.get_rolling_average('s_sema_y', sema_y, window_ms=100)

            if telem_data.hpgHandsOnCyclic:
                hands_on_dict = self.check_hands_on(self.hands_off_deadzone)
            else:
                hands_on_dict = self.check_hands_on(self.hands_on_deadzone)
            hands_on_either = hands_on_dict["master_result"]

            if self.handson_force_mode:
                force_x, force_y = HapticEffect.device.get_input().forceXY()
                thresh = self.hands_on_force_threshold

                is_hands_on_now = abs(force_x) > thresh or abs(force_y) > thresh

                now_ms = int(time.perf_counter() * 1000)

                if is_hands_on_now:
                    self._last_hands_on_time_ms = now_ms
                    hands_on_either = True
                    if self.handson_force_debug:
                        utils.dbprint('green', f"hands on: True     (fx: {force_x}, fy: {force_y})")
                        logging.info(f"hands on: True     (fx: {force_x}, fy: {force_y})")

                else:
                    if now_ms - self._last_hands_on_time_ms > self.handsoff_force_duration:
                        if self.handson_force_debug:
                            utils.dbprint('blue', f"hands on: False     (fx: {force_x}, fy: {force_y})")
                            logging.info(f"hands on: False     (fx: {force_x}, fy: {force_y})")
                        hands_on_either = False
                    else:
                        if self.handson_force_debug:
                            utils.dbprint('yellow', f"hands on: Waiting     (fx: {force_x}, fy: {force_y})")
                            logging.info(f"hands on: Waiting     (fx: {force_x}, fy: {force_y})")

            hands_on_x = hands_on_dict["x_result"]
            dev_x = hands_on_dict["x_deviation"]
            dev_x_raw = hands_on_dict["x_deviation_raw"]
            hands_on_y = hands_on_dict["y_result"]
            dev_y = hands_on_dict["y_deviation"]
            dev_y_raw = hands_on_dict["y_deviation_raw"]

            if not trim_reset:

                sx = round(abs(sema_x_avg), 3)
                sy = round(abs(sema_y_avg), 3)

                self.afcsx_step_size = sx * 0.25
                self.afcsy_step_size = sy * 0.25

                ias = telem_data.IAS or 0 #Indicated Airspeed in m/s

                self.afcs_followup_trim_rate = 100
                dt = perftracker.get_time_delta('hpg_perf_tracker')

                telem_data.hpg_perf_tracker = round(dt, 6)

                followup_trim_step_size_raw = self.afcs_followup_trim_rate * dt  # multiply trim rate by frametime to provide reasonably consistent rate regardless of inter-loop timing.

                self.followup_trim_accumulator += followup_trim_step_size_raw  # accumulate fractional steps

                followup_trim_step_size = int(self.followup_trim_accumulator)  # extract integer portion of step size to use on this loop

                self.followup_trim_accumulator -= followup_trim_step_size  # subtract the applied integer value from the accumulation

                telem_data.hpg_followup_step_size = followup_trim_step_size
                telem_data.hpg_followup_step_size_raw = followup_trim_step_size_raw
                telem_data.hpg_followup_trim_accum = self.followup_trim_accumulator

                followup_trim_state = telem_data.get('hpgFollowupTrimMode', 1)

                match followup_trim_state:
                    case 0: # Both
                        activate_followup_trim = True
                    case 1:  # Off
                        activate_followup_trim = False
                    case 2:  # Hover
                        activate_followup_trim = True if ias < 40 * kt2ms else False
                    case 3:  # Cruise
                        activate_followup_trim = True if ias > 40 * kt2ms else False
                    case _:
                        activate_followup_trim = False

                # disable below for testing on ground
                if telem_data.get('SimOnGround', 1):
                    activate_followup_trim = False

                if not (self.hands_on_x_active or self.hands_on_active):
                    if sema_x_avg > 0:
                        self.cpO_x -= self.afcsx_step_size
                    elif sema_x_avg < 0:
                        self.cpO_x += self.afcsx_step_size
                elif activate_followup_trim and (hands_on_x and self.hands_on_active):
                    if dev_x_raw > 0:
                        self.cpO_x += followup_trim_step_size
                    elif dev_x_raw < 0:
                        self.cpO_x -= followup_trim_step_size

                if not (self.hands_on_y_active or self.hands_on_active):
                    if sema_y_avg > 0:
                        self.cpO_y -= self.afcsy_step_size
                    elif sema_y_avg < 0:
                        self.cpO_y += self.afcsy_step_size
                elif activate_followup_trim and (hands_on_y and self.hands_on_active):
                    # trimmed = True
                    if dev_y_raw > 0:
                        self.cpO_y += followup_trim_step_size
                    elif dev_y_raw < 0:
                        self.cpO_y -= followup_trim_step_size

            self.spring_x.cpOffset = round(self.cpO_x)
            self.spring_y.cpOffset = round(self.cpO_y)
            self._spring_handle.setCondition(self.spring_x)
            self._spring_handle.setCondition(self.spring_y)

            self._dispatch_hands_on_state(telem_data, hands_on_dict, hands_on_either)

            self._spring_handle.start()

    @override
    def _update_cyclic_trim(self, telem_data: BaseTelemetryData):
        # Trimming is handled by the AFCS integration - override parent class function
        pass

    @override
    def msfs_update_pedals(self, telem_data: BaseTelemetryData):

        if telem_data.FFBType != 'pedals':
            return

        if self.telemffb_controls_axes and not self.local_disable_axis_control:
            input_data = HapticEffect.device.get_input()
            phys_x, phys_y = input_data.axisXY()
            telem_data.phys_x = phys_x


            if telem_data.hpgFeetOnPedals:
                feet_on_dict = self.check_feet_on(self.feet_off_deadzone)
            else:
                feet_on_dict = self.check_feet_on(self.feet_on_deadzone)
            feet_on_pedals = feet_on_dict['result']
            dev_x = feet_on_dict['deviation']

            if feet_on_pedals:
                self._simconnect.set_simdatum_to_msfs("L:FFB_FEET_ON_PEDALS", 1, units="number")
                self.feet_on_active = True

            else:
                self._simconnect.set_simdatum_to_msfs("L:FFB_FEET_ON_PEDALS", 0, units="number")
                self.feet_on_active = False

            x_scale = clamp(self.rudder_x_axis_scale, 0, 1)

            self._spring_handle.name = "pedal_ap_spring"

            if not self.pedals_init:

                self.spring_x.set_coefficient(self.pedal_spring_coeff_x)
                if telem_data.get("SimOnGround", 1):
                    self.cpO_x = 0
                else:
                    # print(f"last_colelctive_y={self.last_collective_y}")
                    self.cpO_x = round(4096 * self.last_pedal_x)

                self.spring_x.set_coefficient(self.hpg_pedal_spring_gain, True)

                self.spring_x.set_offset(self.cpO_x)

                self._spring_handle.setCondition(self.spring_x)
                # self.damper.damper(coef_x=int(4096 * self.pedal_dampening_gain)).start()
                self._spring_handle.start()
                logging.debug(f"self.cpO_x:{self.cpO_x}, phys_x:{phys_x}")
                if self.cpO_x / 4096 - 0.1 < phys_x < self.cpO_x / 4096 + 0.1:
                    # dont start sending position until physical pedals have centered
                    self.pedals_init = 1
                    logging.info("Pedals Initialized")
                else:
                    return


            sema_yaw = telem_data.hpgSEMAyaw or 0

            sema_yaw_avg = self.smoother.get_rolling_average('s_sema_yaw', sema_yaw, window_ms=100)

            sx = round(abs(sema_yaw_avg), 3)

            self.afcsx_step_size = sx * 0.5

            telem_data._sx = sx
            telem_data._afcsx_step_size = self.afcsx_step_size

            if not (self.feet_on_active):
                if sema_yaw > 0:
                    self.cpO_x -= self.afcsx_step_size
                elif sema_yaw < 0:
                    self.cpO_x += self.afcsx_step_size

            telem_data._cp0_x = self.cpO_x

            self.spring_x.set_offset(round(self.cpO_x))
            self.spring_x.set_coefficient(self.hpg_pedal_spring_gain, True)
            self._spring_handle.setCondition(self.spring_x)
            self._spring_handle.start()

            if self.enable_custom_x_axis:
                x_var = self.custom_x_axis
                x_range = self.raw_x_axis_scale
            else:
                x_var = 'ROTOR_AXIS_TAIL_ROTOR_SET'
                x_range = 16384

            pos_x_pos = utils.scale(phys_x, (-1, 1), (-x_range * x_scale, x_range * x_scale))

            if x_range != 1:
                pos_x_pos = -int(pos_x_pos)
            else:
                pos_x_pos = round(pos_x_pos, 5)

            self.last_pedal_x = phys_x

            self._simconnect.send_event_to_msfs(x_var, pos_x_pos)

    @override
    def msfs_update_collective(self, telem_data: BaseTelemetryData):
        if telem_data.FFBType != 'collective':
            return
        if not self.telemffb_controls_axes and not self.local_disable_axis_control:
            self.flag_error("Aircraft is configured as class HPGHelicopter.  For proper integration, TelemFFB must send axis position to MSFS.\n\nPlease enable 'telemffb_controls_axes' in your config and unbind the collective axes in MSFS settings")
            return
        self._spring_handle.name = "collective_ap_spring"
        # self.damper = effects["collective_damper"].damper()
        force_trim_pressed = False
        if self.spring_mode_is(SpringModeEnum.FORCETRIM) and self.force_trim_button:
            input_data = HapticEffect.device.get_input()
            force_trim_pressed = self.check_button_press(self.force_trim_button, self.collective_ft_use_master_buttons)
            # force_trim_pressed = input_data.isButtonPressed(self.force_trim_button)
            if self._sim_is_msfs() and force_trim_pressed:
                self._simconnect.send_event_to_msfs("AUTO_THROTTLE_DISCONNECT", 1)

        collective_tr = telem_data.hpgCollectiveRelease or 0
        afcs_mode = telem_data.hpgCollectiveAfcsMode or 0
        collective_pos = telem_data.CollectivePos or 0


        input_data = HapticEffect.device.get_input()
        phys_x, phys_y = input_data.axisXY()
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

            self._spring_handle.start(override=True)

            if self.cpO_y/4096 - 0.1 < phys_y < self.cpO_y/4096 + 0.1:
                # Check if phys y position is within %10 of init point
                # dont start sending position until physical stick has centered
                self.collective_init = 1
                logging.info("Collective Initialized")
            else:
                return
        # Only reach here if collective position is initialized
        self.last_collective_y = phys_y

        if afcs_mode == 0:
            if force_trim_pressed:

                self.cpO_y = round(4096*utils.clamp(phys_y, -1, 1))
                self.spring_y.set_offset(self.cpO_y)

                self.spring_y.set_coefficient(self.trim_release_spring_gain, True)

                self._spring_handle.setCondition(self.spring_y)
                self._spring_handle.start(override=True)

                if self.enable_custom_y_axis:
                    y_var = self.custom_y_axis
                    y_range = self.raw_y_axis_scale
                else:
                    y_var = 'AXIS_COLLECTIVE_SET'
                    y_range = 16384

                pos_y_pos = utils.scale(phys_y, (-1, 1), (-y_range, y_range))

                if y_range != 1:
                    pos_y_pos = -int(pos_y_pos)
                else:
                    pos_y_pos = round(pos_y_pos, 5)

                if self.collective_init:
                    self._simconnect.send_event_to_msfs(y_var, pos_y_pos)


            else:
                self.spring_y.set_offset(self.cpO_y)
                # self.damper.damper(coef_y=0).start()
                self.spring_y.set_coefficient(self.collective_ap_spring_gain, True)

                self._spring_handle.setCondition(self.spring_y)
                self._spring_handle.start(override=True)

        else:
            if force_trim_pressed:

                self.cpO_y = round(4096*utils.clamp(phys_y, -1, 1))
                # print(self.cpO_y)
                self.spring_y.set_offset(self.cpO_y)

                # self.damper.damper(coef_y=0).start()
                self.spring_y.set_coefficient(self.trim_release_spring_gain, True)

                self._spring_handle.setCondition(self.spring_y)
                self._spring_handle.start(override=True)

                if self.enable_custom_y_axis:
                    y_var = self.custom_y_axis
                    y_range = self.raw_y_axis_scale
                else:
                    y_var = 'AXIS_COLLECTIVE_SET'
                    y_range = 16384

                pos_y_pos = utils.scale(phys_y, (-1, 1), (-y_range, y_range))

                if y_range != 1:
                    pos_y_pos = -int(pos_y_pos)
                else:
                    pos_y_pos = round(pos_y_pos, 5)

                if self.collective_init:
                    self._simconnect.send_event_to_msfs(y_var, pos_y_pos)

            else:
                collective_pos = telem_data.CollectivePos or 0
                self.cpO_y = round(utils.scale(collective_pos,(0, 1), (4096, -4096)))
                self.spring_y.set_offset(self.cpO_y)
                # self.damper.damper(coef_y=0).start()
                self.spring_y.set_coefficient(self.collective_ap_spring_gain, True)

                self._spring_handle.setCondition(self.spring_y)
                self._spring_handle.start(override=True)

    @override
    def ac_update_vrs_effect(self, telem_data: BaseTelemetryData):
        vrs_onset = telem_data.hpgVRSDatum or 0
        vrs_certain = telem_data.hpgVRSIsInVRS or 0

        if vrs_certain:
            vrs_intensity = 1.0
        elif vrs_onset == 1:
            vrs_intensity = .33
        elif vrs_onset == 2:
            vrs_intensity = .66
        else:
            vrs_intensity = 0

        if vrs_intensity and self.vrs_effect_intensity and self.vrs_effect_enable:
            self.effects["vrs_buffet"].periodic(10, self.vrs_effect_intensity * vrs_intensity, utils.RandomDirectionModulator).start()
        else:
            self.effects.dispose("vrs_buffet")