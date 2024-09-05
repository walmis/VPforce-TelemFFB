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

from telemffb import utils
from telemffb.utils import overrides
from telemffb.hw.ffb_rhino import (EFFECT_SINE, EFFECT_SQUARE, EFFECT_TRIANGLE, EFFECT_SAWTOOTHUP, EFFECT_SAWTOOTHDOWN, HapticEffect)

from telemffb.sim.aircraft_base import AircraftBase, LPFs, effects, perftracker

#unit conversions (to m/s)
knots = 0.514444
kmh = 1.0/3.6
deg = math.pi/180


class Aircraft(AircraftBase):
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

    ####
    #### Beta effects - set to 1 to enable
    gforce_effect_invert_force = 0  # case where "180" degrees does not equal "away from pilot"
    gforce_effect_enable = 0
    gforce_effect_enable_areyoureallysure = 0
    gforce_effect_curvature = 2.2
    gforce_effect_max_intensity = 1.0
    gforce_min_gs = 1.5  # G's where the effect starts playing
    gforce_max_gs = 5.0  # G limit where the effect maxes out at strength defined in gforce_effect_max_intensity

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

    aoa_effect_enabled = 1

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

    ####
    ####
    def __init__(self, name : str, **kwargs):
        super().__init__(name, **kwargs)
        self.spring = effects["spring"].spring()
        # self.damper = effects["damper"].damper()

        self.damage_enable_cmd_sent = 0
        self.pedals_init = 0
        input_data = HapticEffect.device.get_input()
        self.last_device_x, self.last_device_y = input_data.axisXY()
        self.last_pedal_x = self.last_device_x
        self.last_collective_y = self.last_device_y


    @overrides(AircraftBase)
    def on_telemetry(self, telem_data : dict):
        ## Generic Aircraft Telemetry Handler
        """when telemetry frame is received, aircraft class receives data in dict format

        :param new_data: New telemetry data
        :type new_data: dict
        """

        try:
            j = json.loads(telem_data["MechInfo"])
            out = utils.flatten_dict(j, "", "_")
            for k, v in out.items():
                telem_data[k] = v
            del telem_data["MechInfo"]
        except:
            pass

        if not self.damage_enable_cmd_sent and self.damage_effect_enabled:
            self.send_commands([f"enableGetDamage({int(self.damage_effect_enabled)})"])
            logging.info(f"Sending <enableGetDamage({int(self.damage_effect_enabled)}) to DCS")
            self.damage_enable_cmd_sent = 1



        if not "AircraftClass" in telem_data:
            telem_data["AircraftClass"] = "GenericAircraft"   #inject aircraft class into telemetry

        self._telem_data = telem_data
        if telem_data.get("N") == None:
            return

        if not self.cp_spr_override_active:
            self._update_runway_rumble(telem_data)
            self._decel_effect(telem_data)

        self._update_buffeting(telem_data)
        self._update_cm_weapons(telem_data)
        hyd_loss = self._update_hydraulic_loss_effect(telem_data)
        if not hyd_loss: 
            self._update_ffb_forces(telem_data)
        self._update_damage(telem_data)
        self._update_speed_brakes(telem_data.get("speedbrakes_value"), telem_data.get("TAS"))
        self._update_landing_gear(telem_data.get("gear_value"), telem_data.get("TAS"))
        self._update_flaps(telem_data.get("flaps_value"))
        self._update_canopy(telem_data.get("canopy_value"))
        self._update_spoiler(telem_data.get("Spoilers"), telem_data.get("TAS"))
        self._update_jet_engine_rumble(telem_data)
        if self.is_joystick():
            self._update_stick_position(telem_data)
        if self.is_pedals():
            self._override_pedal_spring(telem_data)
        if self.is_collective():
            self._override_collective_spring(telem_data)
        self._update_tailhook_effect(telem_data)
        self._update_fuelboom_effect(telem_data)
        self._update_wingfold_effect(telem_data)
        self._update_touchdown_effect(telem_data)
        self._update_stick_shaker(telem_data)
        self.override_spring()
        self.override_copilot_spring(telem_data)

    @overrides(AircraftBase)
    def on_event(self, event, *args):
        logging.info(f"on_event: {event}")
        if event == "Stop":
            effects.clear()

    @overrides(AircraftBase)
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


    def send_commands(self, cmds):
        cmds = "\n".join(cmds)
        if not getattr(self, "_socket", None):
            self._socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, 0)
        
        self._socket.sendto(bytes(cmds, "utf-8"), ("127.0.0.1", 34381))

    def _update_damage(self, telem_data):
        if not self.damage_effect_enabled: return
        damage = telem_data.get("Damage")
        damage_freq = 10
        damage_amp = utils.clamp(self.damage_effect_intensity, 0.0, 1.0)

        if self.anything_has_changed("damage", damage):
            random.seed(time.perf_counter())
            random_dir = random.randint(0, 359)
            random_amp = utils.clamp(random.uniform(damage_amp*0.5, damage_amp*1.5), 0.0, 1.0)
            random_type = random.choice([EFFECT_SQUARE, EFFECT_SINE, EFFECT_TRIANGLE])
            effects["damage"].periodic(damage_freq, random_amp, random_dir, effect_type=random_type, duration=30).start()
            logging.debug(f"Damage effect: dir={random_dir}, amp={random_amp}")
        elif not self.anything_has_changed("damage", damage, delta_ms=50):
            effects.dispose("damage")

    def _override_collective_spring(self, telem_data):
        """
        Overrides the spring on a collective to avoid DCS sending FFB events for the Y Axis.  By default sets gain to 0
        with option to override with gain = 4096
        """
        if not self.is_collective(): return

        self.spring = effects["collective_ap_spring"].spring()
        # self.damper = effects["collective_damper"].damper()
        if not self.force_disable_collective_gain:
            self.spring_y.negativeCoefficient = self.spring_y.positiveCoefficient = 4096
            self.spring_y.cpOffset = 0
            self.spring.setCondition(self.spring_y)
            self.spring.start(override=True)
            return

        if not self.collective_init:
            input_data = HapticEffect.device.get_input()
            phys_x, phys_y = input_data.axisXY()

            self.spring_y.negativeCoefficient = self.spring_y.positiveCoefficient = self.collective_spring_coeff_y
            if max(telem_data.get("WeightOnWheels")):
                self.cpO_y = 4096
            else:
                self.cpO_y = round(4096 * self.last_collective_y)


            self.spring_y.positiveCoefficient = self.spring_y.negativeCoefficient = round(
                4096 * utils.clamp(self.collective_ap_spring_gain, 0, 1))

            self.spring_y.cpOffset = self.cpO_y

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

        input_data = HapticEffect.device.get_input()
        phys_x, phys_y = input_data.axisXY()
        self.cpO_y = round(4096 * utils.clamp(phys_y, -1, 1))
        self.spring_y.cpOffset = self.cpO_y

        # self.damper.damper(coef_y=int(4096 * self.collective_dampening_gain)).start()
        self.spring_y.negativeCoefficient = self.spring_y.positiveCoefficient = 0

        self.spring.setCondition(self.spring_y)
        self.spring.start(override=True)



    def _update_pedal_trim(self, telem_data):
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
        self.spring = effects["pedal_spring"].spring()
        self.spring.setCondition(self.spring_x)
        self.spring.start(override=True)

        self.send_commands([f"LoSetCommand(2003, {x - offs_x})"])

    def _update_stick_position(self, telem_data):
        if not self.is_joystick(): return

        if not self.trim_workaround: return

        if not ("StickX" in telem_data and "StickY" in telem_data): return

        input_data = HapticEffect.device.get_input()
        x, y = input_data.axisXY()
        telem_data["X"] = x
        telem_data["Y"] = y

        self.spring_x.positiveCoefficient = 4096
        self.spring_x.negativeCoefficient = 4096
        self.spring_y.positiveCoefficient = 4096
        self.spring_y.negativeCoefficient = 4096

        # trim signal needs to be slow to avoid positive feedback
        lp_y = LPFs.get("y", 5)
        lp_x = LPFs.get("x", 5)

        # estimate trim from real stick position and virtual stick position
        offs_x = lp_x.update(telem_data['StickX'] - x + lp_x.value)
        offs_y = lp_y.update(telem_data['StickY'] - y + lp_y.value)
        
        self.spring_x.cpOffset = utils.clamp_minmax(round(offs_x * 4096), 4096)
        self.spring_y.cpOffset = utils.clamp_minmax(round(offs_y * 4096), 4096)

        spring = effects["trim_spring"].spring()
        # upload effect parameters to stick
        spring.setCondition(self.spring_x)
        spring.setCondition(self.spring_y)
        # ensure effect is started
        spring.start(override=True)

        # override DCS input and set our own values
        self.send_commands([f"LoSetCommand(2001, {y - offs_y})", 
                            f"LoSetCommand(2002, {x - offs_x})"])

    def _update_stick_shaker(self, telem_data):
        if not self.enable_stick_shaker:
            effects['stick_shaker1'].destroy()
            effects['stick_shaker2'].destroy()
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
            effects['stick_shaker1'].periodic(freq, self.stick_shaker_intensity, dir1, EFFECT_SAWTOOTHUP).start()
            effects['stick_shaker2'].periodic(freq, self.stick_shaker_intensity, dir2, EFFECT_SAWTOOTHDOWN).start()
        else:
            effects['stick_shaker1'].destroy()
            effects['stick_shaker2'].destroy()

    def override_copilot_spring(self, telem_data):
        if not self.is_joystick():return


        seat = telem_data.get("Seat", 0)
        if seat == self.cp_spr_override_pilot_seat_id or not self.cp_spr_override_enabled:
            effects['cp_ovd_spring'].stop()
            self.cp_spr_override_active = False
            # self.spring.stop()
            return


        if self.cp_spr_override_button_enabled:

            if self.cp_spr_override_button == 0:
                self.flag_error("Please bind a button to the Co-Pilot/RIO spring override setting or disable the button control option")
                effects['cp_ovd_spring'].stop()
                self.cp_spr_override_active = False
                return
            if self.override_spring_enabled:
                self.flag_error("Co-Pilot/RIO Spring Override is not compatible with \"DCS Spring Override\"\n  Please disable one or the other")
                effects['cp_ovd_spring'].stop()
                self.cp_spr_override_active = False
                return
            if self.dcs_tr_damper_enabled:
                self.flag_error('Co-Pilot/RIO Spring Override is not compatible with the Trim Release Damper feature.  Please disable one or the other.')
                self.cp_spr_override_active = False
                effects['cp_ovd_spring'].stop()
                return
            if self.trim_workaround:
                self.flag_error('Co-Pilot/RIO Spring Override is not compatible with the Trim Workaround feature.  Please disable one or the other.')
                self.cp_spr_override_active = False
                effects['cp_ovd_spring'].stop()
                return

            input_data = HapticEffect.device.get_input()
            override_pressed = input_data.isButtonPressed(self.cp_spr_override_button)

            if not override_pressed:
                effects['cp_ovd_spring'].stop()
                self.cp_spr_override_active = False
                return

        coeff = int(self.cp_spr_override_spring_gain * 4096)
        self.spring_x.positiveCoefficient = self.spring_x.negativeCoefficient = coeff
        self.spring_y.positiveCoefficient = self.spring_y.negativeCoefficient = coeff
        effects['cp_ovd_spring'].spring(coeff, coeff).start(override=True)
        self.cp_spr_override_active = True

    def override_spring(self):
        if not self.is_joystick(): return
        if not self.override_spring_enabled:
            # If feature disabled, ensure spring is stopped and abort
            effects['dcs_spr_override'].stop()
            return
        # Since override_spring overtakes DCS spring already, both the TR damper and trim workaround features are not
        # needed and would conflict with the override of the spring effect
        if self.dcs_tr_damper_enabled:
            self.flag_error('Override DCS Spring is not compatible with the Trim Release Damper feature.  Please disable one or the other.')
            return
        if self.trim_workaround:
            self.flag_error('Override DCS Spring is not compatible with the Trim Workaround feature.  Please disable one or the other.')
            return
        if self.cp_spr_override_enabled:
            self.flag_error('Override DCS Spring is not compatible with the Co-Pilot/RIO Spring Override feature.  Please disable one or the other.')
            return

        spring = effects['dcs_spr_override'].spring()

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
                self.spring_x.positiveCoefficient = gain
                self.spring_x.negativeCoefficient = gain

                self.spring_y.positiveCoefficient = gain
                self.spring_y.negativeCoefficient = gain

                self.override_spring_cp0_x = round(x * 4096)
                self.spring_x.cpOffset = self.override_spring_cp0_x

                self.override_spring_cp0_y = round(y * 4096)
                self.spring_y.cpOffset = self.override_spring_cp0_y
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
        self.spring_x.positiveCoefficient = self.spring_x.negativeCoefficient = int(self.override_spring_gain * 4096)
        self.spring_y.positiveCoefficient = self.spring_y.negativeCoefficient = int(self.override_spring_gain * 4096)

        spring.setCondition(self.spring_x)
        spring.setCondition(self.spring_y)
        # ensure spring is started with override = true
        spring.start(override=True)

class PropellerAircraft(Aircraft):
    """Generic Class for Prop/WW2 aircraft"""

    engine_max_rpm = 2700                           # Assume engine RPM of 2700 at 'EngRPM' = 1.00 for aircraft not exporting 'ActualRPM' in lua script
    max_aoa_cf_force : float = 0.2 # CF force sent to device at %stall_aoa
    # pedal_spring_mode = 'Static Spring'    ## 0=DCS Default | 1=spring disabled + damper enabled, 2=spring enabled at %100 (overriding DCS) + damper


    # run on every telemetry frame
    def on_telemetry(self, telem_data):
        ## Propeller Aircraft Telemetry Handler
        if telem_data.get("N") == None:
            return
        telem_data["AircraftClass"] = "PropellerAircraft"   #inject aircraft class into telemetry
        if not "ActualRPM" in telem_data:
            rpm = telem_data.get("EngRPM", 0)
            if isinstance(rpm, list):
                rpm = [(x / 100) * self.engine_max_rpm for x in rpm]
            else:
                rpm = (rpm / 100) * self.engine_max_rpm
            telem_data["ActualRPM"] = rpm # inject ActualRPM into telemetry

        super().on_telemetry(telem_data)
        
        if self.is_joystick():
            self.override_elevator_droop(telem_data)

        self.update_piston_engine_rumble(telem_data)
        
        self._update_wind_effect(telem_data)
        if self.aoa_effect_enabled:
            # ac_perf = self.get_aircraft_perf(telem_data)
            vs0 = self.aircraft_vs_speed
            vne = self.aircraft_vne_speed
            # print(f"Got Vs0={vs0}, Vne={vne}")
            self._update_aoa_effect(telem_data, minspeed=vs0, maxspeed=vne)
        if not self.cp_spr_override_active:
            self._gforce_effect(telem_data)



class JetAircraft(Aircraft):
    """Generic Class for Jets"""
    #flaps_motion_intensity = 0.0

    _ab_is_playing = 0
    pedal_spring_mode = 'Static Spring'    ## 0=DCS Default | 1=spring disabled + damper enabled, 2=spring enabled at %100 (overriding DCS) + damper

    jet_engine_rumble_intensity = 0.05
    afterburner_effect_intensity = 0.2
    
    # run on every telemetry frame
    def on_telemetry(self, telem_data):
        ## Jet Aircraft Telemetry Handler
        if telem_data.get("N")== None:
            return
        telem_data["AircraftClass"] = "JetAircraft"   #inject aircraft class into telemetry
        super().on_telemetry(telem_data)

        self._update_ab_effect(telem_data)
        if self.aoa_reduction_effect_enabled:
            self._aoa_reduction_force_effect(telem_data)
        if not self.cp_spr_override_active:
            self._gforce_effect(telem_data)

class Helicopter(Aircraft):
    """Generic Class for Helicopters"""
    buffeting_intensity = 0.0

    pedal_spring_mode = 'No Spring'    ## 0=DCS Default | 1=spring disabled + damper enabled, 2=spring enabled at %100 (overriding DCS) + damper

    def on_telemetry(self, telem_data):
        self.speedbrake_motion_intensity = 0.0
        ## Helicopter Aircraft Telemetry Handler
        if telem_data.get("N") == None:
            return
        telem_data["AircraftClass"] = "Helicopter"   #inject aircraft class into telemetry
        super().on_telemetry(telem_data)

        self._calc_etl_effect(telem_data, blade_ct=self.rotor_blade_count)
        self._update_heli_engine_rumble(telem_data, blade_ct=self.rotor_blade_count)
        self._update_vrs_effect(telem_data)
        self.update_tr_damper()

        if not self.cp_spr_override_active:
            self._gforce_effect(telem_data)


    def update_tr_damper(self):
        if not self.is_joystick(): return
        if not self.dcs_tr_damper_enabled: return
        if not self.dcs_tr_button: return

        input_data = HapticEffect.device.get_input()
        force_trim_pressed = input_data.isButtonPressed(self.dcs_tr_button)

        if force_trim_pressed:
            x, y = input_data.axisXY()
            coeff = int(self.dcs_tr_damper_force * 4096)
            self.spring_x.positiveCoefficient = self.spring_x.negativeCoefficient = coeff
            self.spring_y.positiveCoefficient = self.spring_y.negativeCoefficient = coeff
            self.spring_x.cpOffset = int(x * 4096)
            self.spring_y.cpOffset = int(y * 4096)

            # tr_spring = effects['TR Damper'].spring(coeff, coeff)
            self.spring.setCondition(self.spring_x)
            self.spring.setCondition(self.spring_y)
            self.spring.start(override=True)
        else:
            self.spring.stop()

