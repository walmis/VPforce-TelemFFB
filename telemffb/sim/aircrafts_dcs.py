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
 
import json
import logging
import math
import random
import socket
import time
from typing import override

from telemffb import utils
from telemffb.hw.ffb_rhino import (EFFECT_SINE, EFFECT_SQUARE, EFFECT_TRIANGLE, EFFECT_SAWTOOTHUP, EFFECT_SAWTOOTHDOWN, HapticEffect)
from telemffb.sim.aircraft_base import AircraftBase
from telemffb.telem.DcsIpcThread import DcsIpcThread
from telemffb.SettingsManager import SpringModeEnum

from telemffb.util.conversions import kt2ms, kmh2ms, ms2kmh, deg

LPFs = utils.Dispenser(utils.LowPassFilter)
perftracker = utils.PerformanceTracker()

class DCSCommands:
    def dcs_send_commands(self, cmds):
        cmds = "\n".join(cmds)
        DcsIpcThread.send_commands(cmds)

    def dcs_cmd_set_rudder(self, value: float):
        """Sets the rudder position in DCS.  Value is between -1.0 and 1.0

        :param value: Rudder position
        :type value: float
        :return: DCS command string
        :rtype: str
        """
        cmd = f"LoSetCommand(2003, {value})"
        self.dcs_send_commands([cmd])

    def dcs_cmd_set_stick_x(self, value: float):
        """Sets the stick X position in DCS.  Value is between -1.0 and 1.0

        :param value: Stick X position
        :type value: float
        :return: DCS command string
        :rtype: str
        """
        cmd = f"LoSetCommand(2002, {value})"
        self.dcs_send_commands([cmd])
    
    def dcs_cmd_set_stick_y(self, value: float):
        """Sets the stick Y position in DCS.  Value is between -1.0 and 1.0

        :param value: Stick Y position
        :type value: float
        :return: DCS command string
        :rtype: str
        """
        cmd = f"LoSetCommand(2001, {value})"
        self.dcs_send_commands([cmd])

class Aircraft(AircraftBase, DCSCommands):
    """Base class for Aircraft based FFB"""
    ####
    #### Beta effects - set to 1 to enable
    rotor_blade_count = 2
    deceleration_effect_enable = 0
    deceleration_effect_enable_areyoureallysure = 0
    deceleration_max_force = 0.5
    ###

     # gear_motion_effect_enabled: bool = True
    gear_motion_intensity : float = 0.12      # peak vibration intensity when gear is moving, 0 to disable
    # gear_buffet_effect_enabled: bool = True
    gear_buffet_intensity : float = 0.15      # peak buffeting intensity when gear down during flight,  0 to disable

    ###
    ### AoA reduction force effect
    ###
    aoa_reduction_effect_enabled = 0
    aoa_reduction_max_force = 0.0
    critical_aoa_start = 22
    critical_aoa_max = 25

    elevator_droop_enabled = False
    elevator_droop_force = 0

    trim_workaround = False
    damage_effect_enabled = 0
    damage_effect_intensity: float = 0.0

    force_disable_collective_gain = 1
    collective_dampening_gain = 0
    collective_init = 0
    collective_spring_coeff_y = 0
    last_collective_y = 0
    collective_ap_spring_gain = 4096
    cpO_x = 0
    cpO_y = 0

    dcs_tr_damper_enabled = False
    dcs_tr_button = 0
    dcs_tr_damper_force = 0.3

    override_spring_enabled = False
    override_spring_gain = 1.0
    override_spring_ft_enabled = False
    override_spring_tr_damper = 0.5
    override_spring_trim_release = 0
    override_spring_trim_reset = 0
    override_spring_trim_down = 0
    override_spring_trim_left = 0
    override_spring_trim_up = 0
    override_spring_trim_right = 0
    override_spring_trim_rate = 200
    override_spring_cp0_x = 0
    override_spring_cp0_y = 0

    cp_spr_override_enabled = False
    cp_spr_override_pilot_seat_id = 0
    cp_spr_override_spring_gain = 0
    cp_spr_override_button_enabled = False
    cp_spr_override_button = 0
    cp_spr_override_active = False

    enable_stick_shaker = False
    stick_shaker_intensity = .5
    stick_shaker_aoa = 22.3
    stick_shaker_frequency = 40

    ap_active_deadzone_enabled : bool = False
    ap_active_deadzone : float = 0.0

    ####
    ####
    def __init__(self, name : str, **kwargs):
        super().__init__(name, **kwargs)
        self.spring = self.effects["spring"].spring()
        # self.damper = effects["damper"].damper()

        self.damage_enable_cmd_sent = 0
        self.pedals_init = 0
        self.last_device_x, self.last_device_y = HapticEffect.device.get_input().axisXY()
        self.last_pedal_x = self.last_device_x
        self.last_collective_y = None

    @override
    def ac_update_gforce_effect(self, telem_data, adv_spr=False):
        # don't run if override is active
        if self.cp_spr_override_active: return
        return super().ac_update_gforce_effect(telem_data, adv_spr)
    
    @override
    def ac_update_runway_rumble(self, telem_data):
        if self.cp_spr_override_active: return
        return super().ac_update_runway_rumble(telem_data)

    @override
    def ac_update_decel_effect(self, telem_data):
        if self.cp_spr_override_active: return
        return super().ac_update_decel_effect(telem_data)


    @override
    def on_telemetry(self, telem_data : dict):
        ## Generic Aircraft Telemetry Handler
        """when telemetry frame is received, aircraft class receives data in dict format

        :param new_data: New telemetry data
        :type new_data: dict
        """
        input_data = HapticEffect.device.get_input()

        cpx, cpy = input_data.CP_XY()
        telem_data['CP_XY'] = f"{cpx}, {cpy}"

        try:
            j = json.loads(telem_data["MechInfo"])
            out = utils.flatten_dict(j, "", "_")
            for k, v in out.items():
                telem_data[k] = v
            del telem_data["MechInfo"]
        except:
            pass

        if not self.damage_enable_cmd_sent and self.damage_effect_enabled:
            self.dcs_send_commands([f"enableGetDamage({int(self.damage_effect_enabled)})"])
            logging.info(f"Sending <enableGetDamage({int(self.damage_effect_enabled)}) to DCS")
            self.damage_enable_cmd_sent = 1



        if not "AircraftClass" in telem_data:
            telem_data["AircraftClass"] = "GenericAircraft"   #inject aircraft class into telemetry

        self._telem_data = telem_data
        if telem_data.get("N") == None:
            return
        
        # call base class telemetry handler
        super().on_telemetry(telem_data)

        # run dcs specific telemetry handlers
        if self.is_joystick():
            self.dcs_update_stick_position(telem_data)

        if self.is_collective():
            self.dcs_override_collective_spring(telem_data)

        self.dcs_update_damage(telem_data)
        self.dcs_update_stick_shaker(telem_data)
        self.dcs_override_spring()
        self.dcs_override_copilot_spring(telem_data)
        self.dcs_update_ap_deadzone(telem_data)

    @override
    def on_event(self, event, *args):
        logging.info(f"on_event: {event}")
        if event == "Stop":
            self.effects.clear()

    @override
    def on_timeout(self):
        super().on_timeout()
        input_data = HapticEffect.device.get_input()
        self.last_device_x, self.last_device_y = input_data.axisXY()
        self.last_pedal_x = self.last_device_x
        self.last_collective_y = self.last_device_y
        self.damage_enable_cmd_sent = 0
        self.collective_init = 0
        self.pedals_init = 0
        # self.spring.stop()
        # self.damper.stop()




    def dcs_update_damage(self, telem_data):
        if not self.damage_effect_enabled: return
        damage = telem_data.get("Damage")
        damage_freq = 10
        damage_amp = utils.clamp(self.damage_effect_intensity, 0.0, 1.0)

        if self.anything_has_changed("damage", damage):
            random.seed(time.perf_counter())
            random_dir = random.randint(0, 359)
            random_amp = utils.clamp(random.uniform(damage_amp*0.5, damage_amp*1.5), 0.0, 1.0)
            random_type = random.choice([EFFECT_SQUARE, EFFECT_SINE, EFFECT_TRIANGLE])
            self.effects["damage"].periodic(damage_freq, random_amp, random_dir, effect_type=random_type, duration=30).start()
            logging.debug(f"Damage effect: dir={random_dir}, amp={random_amp}")
        elif not self.anything_has_changed("damage", damage, delta_ms=50):
            self.effects.dispose("damage")

    def dcs_override_collective_spring(self, telem_data):
        """
        Overrides the spring on a collective to avoid DCS sending FFB events for the Y Axis.  By default sets gain to 0
        with option to override with gain = 4096
        """
        if not self.is_collective(): return

        # self.damper = effects["collective_damper"].damper()
        if not self.force_disable_collective_gain:
            self.spring_y.set_coefficient(1.0)
            self.spring_y.set_offset(0)
            self.spring.setCondition(self.spring_y)
            self.spring.start(override=True)
            return

        input_data = HapticEffect.device.get_input()
        phys_x, phys_y = input_data.axisXY()

        if not self.collective_init:
            self.spring = self.effects["collective_ap_spring"].spring()

            self.spring_y.set_coefficient(1.0)
            if max(telem_data.get("WeightOnWheels")):
                self.cpO_y = 4096
            elif self.last_collective_y is None:
                # Air start or new aircraft.  Use current physical position as init point
                self.cpO_y = round(4096 * phys_y)
            else:
                self.cpO_y = round(4096 * self.last_collective_y)

            self.spring_y.set_offset(self.cpO_y)

            self.spring.setCondition(self.spring_y)
            # self.damper.damper(coef_y=int(4096 * self.collective_dampening_gain)).start()
            self.spring.start(override=True)
            # print(f"self.cpO_y:{self.cpO_y}, phys_y:{phys_y}")
            if self.cpO_y / 4096 - 0.1 < phys_y < self.cpO_y / 4096 + 0.1:
                # dont start sending position until physical stick has centered
                self.collective_init = 1
                logging.info("Collective Initialized")
            else:
                return
        self.last_collective_y = phys_y

        if self.spring_mode_is(SpringModeEnum.FORCETRIM):
            self.spring.name = "collective_ft"
            self.ac_collective_force_trim_override(telem_data, self.spring)
        else:
            self.spring.name = "collective_ap_spring"
            self.cpO_y = round(4096 * utils.clamp(phys_y, -1, 1))
            self.spring_y.set_offset(self.cpO_y)

            # self.damper.damper(coef_y=int(4096 * self.collective_dampening_gain)).start()
            self.spring_y.set_coefficient(0)

            self.spring.setCondition(self.spring_y)
            self.spring.start(override=True)


    @override
    def ac_update_pedal_trim(self, telem_data):
        return self.dcs_update_pedal_trim(telem_data)

    def dcs_update_pedal_trim(self, telem_data):
        if not self.is_pedals(): return

        input_data = HapticEffect.device.get_input()
        x, y = input_data.axisXY()
        telem_data["X"] = x

        pedal_pos = -telem_data.get('controlsurfaces_rudder_right',0)
        # trim signal needs to be slow to avoid positive feedback
        lp_x = LPFs.get("x", 5)
        # estimate trim from real stick position and virtual stick position
        offs_x = lp_x.update(pedal_pos - x - lp_x.value)
        self.spring_x.cpOffset = utils.clamp_minmax(round(offs_x * 4096), 4096)
        self.spring = self.effects["pedal_spring"].spring()
        self.spring.setCondition(self.spring_x)
        self.spring.start(override=True)

        self.dcs_send_commands([f"LoSetCommand(2003, {x - offs_x})"])

    def dcs_update_stick_position(self, telem_data):
        if not self.is_joystick(): return

        if not self.trim_workaround: return

        if not ("StickX" in telem_data and "StickY" in telem_data): return

        input_data = HapticEffect.device.get_input()
        x, y = input_data.axisXY()
        telem_data["X"] = x
        telem_data["Y"] = y

        self.spring_x.set_coefficient(1.0)
        self.spring_y.set_coefficient(1.0)

        # trim signal needs to be slow to avoid positive feedback
        lp_y = LPFs.get("y", 5)
        lp_x = LPFs.get("x", 5)

        # estimate trim from real stick position and virtual stick position
        offs_x = lp_x.update(telem_data['StickX'] - x + lp_x.value)
        offs_y = lp_y.update(telem_data['StickY'] - y + lp_y.value)

        self.spring_x.set_offset(offs_x)
        self.spring_y.set_offset(offs_y)

        spring = self.effects["trim_spring"].spring()
        # upload effect parameters to stick
        spring.setCondition(self.spring_x)
        spring.setCondition(self.spring_y)
        # ensure effect is started
        spring.start(override=True)

        # override DCS input and set our own values
        self.dcs_send_commands([f"LoSetCommand(2001, {y - offs_y})", 
                            f"LoSetCommand(2002, {x - offs_x})"])

    def dcs_update_stick_shaker(self, telem_data):
        if not self.enable_stick_shaker:
            self.effects['stick_shaker1'].destroy()
            self.effects['stick_shaker2'].destroy()
            return

        aoa = telem_data.get('AoA', 0)
        on_ground = telem_data.get('SimOnGround', True)
        if aoa > self.stick_shaker_aoa and not on_ground:
            shake = True
        else:
            shake = False

        if self.is_joystick():
            dir1 = 0
            dir2 = 180
        elif self.is_pedals():
            dir1 = 90
            dir2 = 270
        else:
            return

        if shake:
            freq = self.stick_shaker_frequency
            self.effects['stick_shaker1'].periodic(freq, self.stick_shaker_intensity, dir1, EFFECT_SAWTOOTHUP).start()
            self.effects['stick_shaker2'].periodic(freq, self.stick_shaker_intensity, dir2, EFFECT_SAWTOOTHDOWN).start()
        else:
            self.effects['stick_shaker1'].destroy()
            self.effects['stick_shaker2'].destroy()

    def dcs_update_ap_deadzone(self, telem_data):
        """
        Updates the dead-zone when the AP is active.  Useful for aircraft that are sensitive to joystick input when AP is active.

        Args:
            telem_data:  Expects 'APEnabled' key in the dictionary

        Returns: None

        """
        if not self.is_joystick(): return

        ap_active = telem_data.get("APEnabled", 0)

        if not self.ap_active_deadzone_enabled:
            self.ac_set_deadzone_override(0)
            return

        if ap_active:
            self.ac_set_deadzone_override(self.ap_active_deadzone)
        else:
            self.ac_set_deadzone_override(0)


    def dcs_override_copilot_spring(self, telem_data):
        if not self.is_joystick():return


        seat = telem_data.get("Seat", 0)
        if seat == self.cp_spr_override_pilot_seat_id or not self.cp_spr_override_enabled:
            self.effects['cp_ovd_spring'].stop()
            self.cp_spr_override_active = False
            # self.spring.stop()
            return

        if self.dcs_tr_damper_enabled:
            self.flag_error(
                'Co-Pilot/RIO Spring Override is not compatible with the Trim Release Damper feature.  Please disable one or the other.')
            self.cp_spr_override_active = False
            self.effects['cp_ovd_spring'].stop()
            return
        if self.trim_workaround:
            self.flag_error(
                'Co-Pilot/RIO Spring Override is not compatible with the Trim Workaround feature.  Please disable one or the other.')
            self.cp_spr_override_active = False
            self.effects['cp_ovd_spring'].stop()
            return

        if self.cp_spr_override_button_enabled:

            if self.cp_spr_override_button == 0:
                self.flag_error("Please bind a button to the Co-Pilot/RIO spring override setting or disable the button control option")
                self.effects['cp_ovd_spring'].stop()
                self.cp_spr_override_active = False
                return

            input_data = HapticEffect.device.get_input()
            override_pressed = input_data.isButtonPressed(self.cp_spr_override_button)

            if not override_pressed:
                self.effects['cp_ovd_spring'].stop()
                self.cp_spr_override_active = False
                return

        coeff = int(self.cp_spr_override_spring_gain * 4096)
        self.spring_x.set_coefficient(coeff)
        self.spring_y.set_coefficient(coeff)
        self.effects['cp_ovd_spring'].spring(coeff, coeff).start(override=True)
        self.cp_spr_override_active = True

    def dcs_override_spring(self):
        if not self.is_joystick(): return
        if not self.spring_mode_is(SpringModeEnum.CUSTOM):
            # If feature disabled, ensure spring is stopped and abort
            self.effects['dcs_spr_override'].stop()
            return

        if self.trim_workaround:
            self.flag_error('Override DCS Spring is not compatible with the Trim Workaround feature.  Please disable one or the other.')
            return


        spring = self.effects['dcs_spr_override'].spring()

        dt = perftracker.get_time_delta('override_spring_perf')
        self.telem_data['_ovrd_spr_dt'] = dt

        if self.override_spring_ft_enabled:
            input_data = HapticEffect.device.get_input()
            x, y = input_data.axisXY()
            current_buttons = input_data.getPressedButtons()
            # print(f"BUTTONS:>{current_buttons}<")
            # decide what to do depending on which button is pressed
            if self.override_spring_trim_release and self.override_spring_trim_release in current_buttons:
                # use spring force as dampening.  Configured damper value applied as spring gain.  cpO will follow stick
                # as it is moved while spring force is enabled.
                # return from method so default spring gains do not get applied at the end of the method
                gain = int (self.override_spring_tr_damper * 4096)
                self.spring_x.set_coefficient(gain)
                self.spring_y.set_coefficient(gain)

                self.override_spring_cp0_x = round(x * 4096)
                self.spring_x.set_offset(self.override_spring_cp0_x)

                self.override_spring_cp0_y = round(y * 4096)
                self.spring_y.set_offset(self.override_spring_cp0_y)
                spring.setCondition(self.spring_x)
                spring.setCondition(self.spring_y)
                spring.start(override=True)
                return
            
            elif self.override_spring_trim_reset and self.override_spring_trim_reset in current_buttons:
                # if trim reset button pressed, set offsets back to 0
                # print("TRIM RESET")
                self.spring_x.cpOffset = self.override_spring_cp0_x = 0
                self.spring_y.cpOffset = self.override_spring_cp0_y = 0
                spring.setCondition(self.spring_x)
                spring.setCondition(self.spring_y)

            # calculate step size based on configured rate and delta time
            trim_step_size = self.override_spring_trim_rate * dt

            self.telem_data['_ovrd_spr_step'] = trim_step_size

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

        self.telem_data['_ovrd_spr_trim_pos'] = [round(self.override_spring_cp0_x), round(self.override_spring_cp0_y)]

        # If trim release is not pressed, set spring gain based on user setting and start spring override
        self.spring_x.set_coefficient(self.override_spring_gain)
        self.spring_y.set_coefficient(self.override_spring_gain)

        spring.setCondition(self.spring_x)
        spring.setCondition(self.spring_y)
        # ensure spring is started with override = true
        spring.start(override=True)

class PropellerAircraft(Aircraft):
    """Generic Class for Prop/WW2 aircraft"""

    engine_max_rpm = 2700                           # Assume engine RPM of 2700 at 'EngRPM' = 1.00 for aircraft not exporting 'ActualRPM' in lua script
    max_aoa_cf_force : float = 0.2 # CF force sent to device at %stall_aoa

    def dcs_update_actual_rpm(self, telem_data):
        if not "ActualRPM" in telem_data:
            rpm = telem_data.get("EngRPM", 0)
            if isinstance(rpm, list):
                rpm = [(x / 100) * self.engine_max_rpm for x in rpm]
            else:
                rpm = (rpm / 100) * self.engine_max_rpm
            telem_data["ActualRPM"] = rpm # inject ActualRPM into telemetry

    # run on every telemetry frame
    @override
    def on_telemetry(self, telem_data):
        ## Propeller Aircraft Telemetry Handler
        if telem_data.get("N") == None:
            return
        telem_data["AircraftClass"] = "PropellerAircraft"   #inject aircraft class into telemetry
        self.dcs_update_actual_rpm(telem_data)

        super().on_telemetry(telem_data)


class JetAircraft(Aircraft):
    """Generic Class for Jets"""
    #flaps_motion_intensity = 0.0

    jet_engine_rumble_intensity = 0.05
    afterburner_effect_intensity = 0.2

    # run on every telemetry frame
    @override
    def on_telemetry(self, telem_data):
        ## Jet Aircraft Telemetry Handler
        if telem_data.get("N")== None:
            return
        telem_data["AircraftClass"] = "JetAircraft"   #inject aircraft class into telemetry
        super().on_telemetry(telem_data)

class TurbopropAircraft(PropellerAircraft):
    def __init__(self, name, **kwargs):
        super().__init__(name, **kwargs)

    @override
    def on_telemetry(self, telem_data):
        if telem_data.get("N") == None:
            return
        telem_data["AircraftClass"] = "TurbopropAircraft"  # inject aircraft class into telemetry

        super().on_telemetry(telem_data)

class Helicopter(Aircraft):
    """Generic Class for Helicopters"""
    buffeting_intensity = 0.0

    @override
    def on_telemetry(self, telem_data):
        self.speedbrake_motion_intensity = 0.0
        ## Helicopter Aircraft Telemetry Handler
        if telem_data.get("N") == None:
            return
        telem_data["AircraftClass"] = "Helicopter"   #inject aircraft class into telemetry
        super().on_telemetry(telem_data)

        self.dcs_update_trim_damper()

    def dcs_update_trim_damper(self):
        if not self.is_joystick(): return
        if not self.spring_mode_is(SpringModeEnum.NONE): return  # only supported when spring mode is NONE (game managed)
        if not self.dcs_tr_damper_enabled: return
        if not self.dcs_tr_button:
            self.flag_error('Please configure the trim-release button.  It must match that which is bound as trim release in the sim.')
            return

        input_data = HapticEffect.device.get_input()
        force_trim_pressed = input_data.isButtonPressed(self.dcs_tr_button)

        if force_trim_pressed:
            x, y = input_data.axisXY()
            self.spring_x.set_coefficient(self.dcs_tr_damper_force)
            self.spring_y.set_coefficient(self.dcs_tr_damper_force)
            self.spring_x.set_offset(x)
            self.spring_y.set_offset(y)

            # tr_spring = effects['TR Damper'].spring(coeff, coeff)
            self.spring.setCondition(self.spring_x)
            self.spring.setCondition(self.spring_y)
            self.spring.start(override=True)
        else:
            self.spring.stop()

