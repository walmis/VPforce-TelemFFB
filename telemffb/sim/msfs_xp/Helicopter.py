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

import telemffb.utils as utils
from telemffb.SettingsManager import SpringModeEnum
from telemffb.hw.ffb_rhino import HapticEffect
from telemffb.sim.msfs_xp.Aircraft import Aircraft
from telemffb.utils import clamp, overrides

import logging

class Helicopter(Aircraft):
    """Generic Class for Helicopters"""
    buffeting_intensity = 0.0

    etl_start_speed = 6.0  # m/s
    etl_stop_speed = 22.0  # m/s
    etl_effect_intensity = 0.2  # [ 0.0 .. 1.0]
    etl_shake_frequency = 14.0  # value has been deprecated in favor of rotor RPM calculation
    overspeed_shake_start = 70.0  # m/s
    overspeed_shake_intensity = 0.2
    heli_engine_rumble_intensity = 0.12

    virtual_cyclic_x_offs = 0
    virtual_cyclic_y_offs = 0
    phys_cyclic_x_offs = 0
    phys_cyclic_y_offs = 0
    stepper_dict = {}
    trim_reset_complete = 1
    last_device_x = 0
    last_device_y = 0
    last_pos_y_pos = 0
    last_pos_x_pos = 0

    collective_init = 0
    collective_ap_spring_gain = 1
    collective_dampening_gain = 0
    collective_spring_coeff_y = 0
    last_collective_y = None

    pedal_spring_gain = 1
    hpg_pedal_spring_gain = 1
    pedal_dampening_gain = 1
    pedal_spring_coeff_x = 0

    joystick_trim_follow_gain_physical_x = 0.3
    joystick_trim_follow_gain_virtual_x = 0.2
    joystick_trim_follow_gain_physical_y = 0.3
    joystick_trim_follow_gain_virtual_y = 0.2
    cyclic_physical_trim_x_offs = 0
    cyclic_physical_trim_y_offs = 0
    cyclic_virtual_trim_x_offs = 0
    cyclic_virtual_trim_y_offs = 0

    # Vars for custom force trim switch L:var
    custom_ft_sw_var_enabled = False
    custom_ft_sw_var = "L:TelemFFBHeliFT"

    def __init__(self, name, **kwargs):
        super().__init__(name, **kwargs)
        self.pedals_init = 0
        if self._sim_is_msfs():
            self.subscribe_simvars()

        self.cpO_x = 0
        self.cpO_y = 0

    @overrides(Aircraft)
    def on_timeout(self):
        super().on_timeout()
        self.cyclic_spring_init = 0
        self.collective_init = 0
        self.pedals_init = 0

    @overrides(Aircraft)
    def on_telemetry(self, telem_data):
        self.speedbrake_motion_intensity = 0.0
        if telem_data.get("N") == None:
            return
        telem_data["AircraftClass"] = "Helicopter"  # inject aircraft class into telemetry

        super().on_telemetry(telem_data)

        if self.is_trimwheel():
            return

        self.msfs_update_heli_controls(telem_data)
        self.msfs_update_collective(telem_data)
        # # self._update_cyclic_trim(telem_data)
        self.msfs_update_pedals(telem_data)

    @overrides(Aircraft)
    def msfs_update_trimwheel(self, *args, **kwargs):
        pass

    def subscribe_simvars(self):
        if not self._simconnect:
            return

        if 'ForceTrimSW' not in self._simconnect.sv_dict.keys():
            self._simconnect.add_simvar(name='ForceTrimSW', var="L:TelemFFBHeliFT", sc_unit="enum")
            self._simconnect._resubscribe()

    def msfs_update_heli_controls(self, telem_data):
        ffb_type = telem_data.get("FFBType", "joystick")
        if self._sim_is_msfs():
            ap_active = telem_data.get("APMaster", 0)
        if self._sim_is_xplane():
            ap_active = telem_data.get("APServos", 0)

        if self._sim_is_msfs():
            if (self.custom_ft_sw_var_enabled and self.anything_has_changed('custom_ft_sw_var', self.custom_ft_sw_var)) or self.anything_has_changed('custom_ft_sw_var_enabled', self.custom_ft_sw_var_enabled):
                self._simconnect.add_simvar(name='ForceTrimSW', var=self.custom_ft_sw_var, sc_unit="enum")
                self._simconnect._resubscribe()

        self._spring_handle.name = "cyclic_spring"
        force_trim_active = telem_data.get('ForceTrimSW', True) if self.custom_ft_sw_var_enabled else True  # Enable cockpit switch control (if exists) for force trim.  Add LVar as "ForceTrimSW" bool if available for aircraft
        if ffb_type == "joystick":
            input_data = HapticEffect.device.get_input()
            x, y = input_data.axisXY()
            telem_data['phys_x'] = x
            telem_data['phys_y'] = y
            if self.spring_mode_is(SpringModeEnum.FORCETRIM) and force_trim_active:

                if self.force_trim_button == 0:
                    self.flag_error("Force trim enabled but buttons not configured")
                    return
                if self.cyclic_spring_init:
                    force_trim_pressed = input_data.isButtonPressed(self.force_trim_button)
                else:
                    force_trim_pressed = False

                if self.force_trim_reset_button > 0:
                    trim_reset_pressed = input_data.isButtonPressed(self.force_trim_reset_button)
                else:
                    trim_reset_pressed = False

                self.tr_state_change = False

                if self.anything_has_changed('ft_tracker', force_trim_pressed):
                    self.tr_state_change = True

                # Add: remember previous "pressed" to detect edge
                force_trim_pressed_prev = getattr(self, "force_trim_pressed_prev", False)
                force_trim_pressed = input_data.isButtonPressed(self.force_trim_button) if self.cyclic_spring_init else False
                self.tr_state_change = (force_trim_pressed != force_trim_pressed_prev)
                self.force_trim_pressed_prev = force_trim_pressed

                if not self.cyclic_spring_init:
                    """This clause uses spring force to initialize the cyclic to one of two positions
                    If on the ground, the cyclic will always initialize to center
                    If in the air, the cyclic will initialize to the last position before pause or center if no saved position"""
                    # print("CYCLIC INIT LOOP")
                    self.cyclic_center = [0.0, 0.0]

                    input_data = HapticEffect.device.get_input()


                    # force_trim_pressed = input_data.isButtonPressed(self.force_trim_reset_button)
                    phys_x, phys_y = input_data.axisXY()
                    telem_data['phys_x'] = phys_x
                    telem_data['phys_y'] = phys_y
                    self.spring_x.set_coefficient(self.cyclic_spring_gain, True)
                    self.spring_y.set_coefficient(self.cyclic_spring_gain, True)

                    if telem_data.get("SimOnGround", 1) and self.trim_reset_complete:
                        self.cpO_x = 0
                        self.cpO_y = 0
                        self.last_pos_x_pos = 0
                        self.last_pos_y_pos = 0
                    else:
                        self.cpO_x = round(self.last_device_x * 4096)
                        self.cpO_y = round(self.last_device_y * 4096)
                        # utils.dbprint('yellow', f"CyclicInit - cpOy={self.cpO_y} cpOx={self.cpO_x}")

                    self.spring_x.cpOffset = self.cpO_x
                    self.spring_y.cpOffset = self.cpO_y
                    self._spring_handle.setCondition(self.spring_x)
                    self._spring_handle.setCondition(self.spring_y)
                    self._spring_handle.start()
                    if (self.cpO_x/4096 - 0.15 < phys_x < self.cpO_x/4096 + 0.15) and (self.cpO_y/4096 - 0.15 < phys_y < self.cpO_y/4096 + 0.15):
                        #dont start sending position until physical stick has centered
                        self.cyclic_spring_init = 1
                        logging.info("Cyclic Spring Initialized")
                    else:
                        if self._sim_is_msfs():
                            if self.enable_custom_x_axis:
                                x_var = self.custom_x_axis
                            else:
                                x_var = 'AXIS_CYCLIC_LATERAL_SET'
                            if self.enable_custom_y_axis:
                                y_var = self.custom_y_axis
                            else:
                                y_var = 'AXIS_CYCLIC_LONGITUDINAL_SET'

                            self._simconnect.send_event_to_msfs(x_var, self.last_pos_x_pos)
                            self._simconnect.send_event_to_msfs(y_var, self.last_pos_y_pos)
                        return
                elif force_trim_pressed:
                    """This clause executes when the force trim button is depressed
                    It calculates the total offset including the trim data
                    It applies a 'following spring' that induces a damper effect on the joystick"""
                    if self.tr_state_change:
                        # compute total center currently applied to device
                        total_x = int(self.cpO_x) + int(self.cyclic_physical_trim_x_offs)
                        total_y = int(self.cpO_y) + int(self.cyclic_physical_trim_y_offs)

                        # move that total into cpO_*, then zero trim-following contributions
                        self.cpO_x = total_x
                        self.cpO_y = total_y
                        self.cyclic_physical_trim_x_offs = 0
                        self.cyclic_physical_trim_y_offs = 0
                        self.cyclic_virtual_trim_x_offs = 0.0
                        self.cyclic_virtual_trim_y_offs = 0.0

                        # tell MSFS to reset rotor trim ONCE on edge
                        if self._sim_is_msfs():
                            self._simconnect.send_event_to_msfs("ROTOR_TRIM_RESET", 1)

                        logging.info(f"Force Trim Disengaged total={self.cpO_x}:{self.cpO_y}")

                    # Soften the spring while held (keep your scaling if that's intentional)
                    gain = int(self.trim_release_spring_gain * 4096)
                    self.spring_x.set_coefficient(gain)
                    self.spring_y.set_coefficient(gain)

                    # Continuously re-center spring to live stick while held
                    self.cpO_x = round(x * 4096)
                    self.cpO_y = round(y * 4096)

                    self.spring_x.cpOffset = self.cpO_x
                    self.spring_y.cpOffset = self.cpO_y

                    self.cyclic_center = [x, y]
                    self.cyclic_trim_release_active = 1

                elif not force_trim_pressed and self.cyclic_trim_release_active:
                    """This clause executes when the force trim button is released
                    It reapplies the spring force with the spring center at the current stick position"""
                    # --- on release: restore normal spring gain and lock new center ---
                    self.spring_x.set_coefficient(self.cyclic_spring_gain, True)
                    self.spring_y.set_coefficient(self.cyclic_spring_gain, True)

                    self.cpO_x = round(x * 4096)
                    self.cpO_y = round(y * 4096)
                    self.spring_x.set_offset(self.cpO_x)
                    self.spring_y.set_offset(self.cpO_y)

                    self.cyclic_center = [x, y]

                    if self._sim_is_msfs():
                        # turn off the reset flag ONCE on edge
                        self._simconnect.send_event_to_msfs("ROTOR_TRIM_RESET", 0)

                    logging.info(f"Force Trim Engaged :{self.cpO_x}:{self.cpO_y}")
                    self.cyclic_trim_release_active = 0

                elif trim_reset_pressed or not self.trim_reset_complete:
                    """This clause executes when the trim reset button is pressed
                    It drives the stock back to center over a 500ms period"""

                    if not hasattr(self, '_trim_reset_in_progress'):
                        self._trim_reset_in_progress = True
                        logging.info("Trim Reset Pressed")


                    self.cpO_x = self.step_value_over_time("center_x", self.cpO_x, 500, 0)
                    self.cpO_y = self.step_value_over_time("center_y", self.cpO_y, 500, 0)

                    if self.cpO_x == 0 and self.cpO_y == 0:
                        self.trim_reset_complete = 1
                        if self._trim_reset_in_progress:
                            logging.info("Trim Reset Complete")
                        self._trim_reset_in_progress = False
                    else:
                        self.trim_reset_complete = 0
                        if not self._trim_reset_in_progress:
                            logging.info("Trim Reset Pressed")
                        self._trim_reset_in_progress = True

                    self.spring_x.set_coefficient(self.cyclic_spring_gain)
                    self.spring_y.set_coefficient(self.cyclic_spring_gain)

                    self.spring_x.set_offset(round(self.cpO_x))
                    self.spring_y.set_offset(round(self.cpO_y))

                    if self._sim_is_msfs():
                        self._simconnect.send_event_to_msfs("ROTOR_TRIM_RESET", 0)

                else:
                    if getattr(self, 'ft_was_inactive', True):
                        self.spring_x.set_coefficient(self.cyclic_spring_gain, True)
                        self.spring_y.set_coefficient(self.cyclic_spring_gain, True)
                        self.ft_was_inactive = False

                telem_data["StickXY"] = [x, y]
                telem_data["StickXY_offset"] = self.cyclic_center

            elif self.spring_mode_is(SpringModeEnum.FORCETRIM) and not force_trim_active:
                self.ft_was_inactive = True

                gain = int(self.trim_release_spring_gain * 4096)
                self.spring_x.set_coefficient(gain)
                self.spring_y.set_coefficient(gain)

                # Continuously re-center spring to live stick while held
                self.cpO_x = round(x * 4096)
                self.cpO_y = round(y * 4096)

                self.spring_x.cpOffset = self.cpO_x
                self.spring_y.cpOffset = self.cpO_y

                self.cyclic_center = [x, y]

            else:
                self._spring_handle.stop()

            if self.telemffb_controls_axes and not self.local_disable_axis_control:
                input_data = HapticEffect.device.get_input()
                phys_x, phys_y = input_data.axisXY()
                telem_data['phys_x'] = phys_x
                telem_data['phys_y'] = phys_y
                self._update_cyclic_trim(telem_data)

                x_pos = phys_x - self.cyclic_virtual_trim_x_offs
                y_pos = phys_y - self.cyclic_virtual_trim_y_offs

                x_scale = clamp(self.joystick_x_axis_scale, 0, 1)
                y_scale = clamp(self.joystick_y_axis_scale, 0, 1)

                if self._sim_is_xplane():
                    pos_x_pos = x_pos * x_scale
                    pos_y_pos = y_pos * y_scale
                    self.send_xp_command(f'AXIS:jx={round(pos_x_pos, 5)},jy={round(pos_y_pos, 5)}')

                if self.cyclic_spring_init or not (self.spring_mode_is(SpringModeEnum.FORCETRIM) and force_trim_active):
                    if self._sim_is_msfs():
                        if self.enable_custom_x_axis:
                            x_var = self.custom_x_axis
                            x_range = self.raw_x_axis_scale
                        else:
                            x_var = 'AXIS_CYCLIC_LATERAL_SET'
                            x_range = 16384
                        if self.enable_custom_y_axis:
                            y_var = self.custom_y_axis
                            y_range = self.raw_y_axis_scale
                        else:
                            y_var = 'AXIS_CYCLIC_LONGITUDINAL_SET'
                            y_range = 16384

                        pos_x_pos = utils.scale(x_pos, (-1, 1), (-x_range * x_scale, x_range * x_scale))
                        pos_y_pos = utils.scale(y_pos, (-1, 1), (-y_range * y_scale, y_range * y_scale))

                        if x_range != 1:
                            pos_x_pos = -int(pos_x_pos)
                        else:
                            pos_x_pos = round(pos_x_pos, 5)
                        if y_range != 1:
                            pos_y_pos = -int(pos_y_pos)
                        else:
                            pos_y_pos = round(pos_y_pos, 5)

                        self._simconnect.send_event_to_msfs(x_var, pos_x_pos)
                        self._simconnect.send_event_to_msfs(y_var, pos_y_pos)
                        self.last_pos_x_pos = pos_x_pos
                        self.last_pos_y_pos = pos_y_pos

                self.last_device_x, self.last_device_y = phys_x, phys_y

            if self.anything_has_changed("cyclic_gain", self.cyclic_spring_gain):  # check if spring gain setting has been modified in real time
                self.spring_x.set_coefficient(self.cyclic_spring_gain)
                self.spring_y.set_coefficient(self.cyclic_spring_gain)

            self.spring_x.set_offset(int(self.cpO_x) + self.cyclic_physical_trim_x_offs)
            self.spring_y.set_offset(int(self.cpO_y) + self.cyclic_physical_trim_y_offs)
            self._spring_handle.setCondition(self.spring_x)
            self._spring_handle.setCondition(self.spring_y)
            if self.spring_mode_is(SpringModeEnum.FORCETRIM) and force_trim_active:
                if not self._spring_handle.started:
                    self._spring_handle.start()

    def _update_cyclic_trim(self, telem_data):
        if telem_data.get("FFBType", None) != 'joystick':
            return
        if not self.trim_following:
            return

        if not telem_data.get('ForceTrimSW', True):
            self.cyclic_physical_trim_x_offs = 0
            self.cyclic_physical_trim_y_offs = 0
            self.cyclic_virtual_trim_x_offs = 0
            self.cyclic_virtual_trim_y_offs = 0
            return

        # NEW: don't mutate offsets while trim-release is active
        if getattr(self, "cyclic_trim_release_active", 0):
            return
        if self._sim_is_msfs():
            cyclic_x_trim = telem_data.get("CyclicTrimX", 0)
            cyclic_y_trim = telem_data.get("CyclicTrimY", 0)
        if self._sim_is_xplane():
            cyclic_x_trim = telem_data.get("AileronTrimPct", 0)
            cyclic_y_trim = telem_data.get("ElevTrimPct", 0)


        cyclic_x_trim = clamp(cyclic_x_trim * self.joystick_trim_follow_gain_physical_x * self.joystick_x_axis_scale, -1, 1)
        cyclic_y_trim = clamp(cyclic_y_trim * self.joystick_trim_follow_gain_physical_y * self.joystick_y_axis_scale, -1, 1)

        # print(f"x:{cyclic_x_trim}, y:{cyclic_y_trim}")

        self.cyclic_physical_trim_x_offs = round(cyclic_x_trim * 4096)
        self.cyclic_physical_trim_y_offs = round(cyclic_y_trim * 4096)
        self.cyclic_virtual_trim_x_offs = cyclic_x_trim - (cyclic_x_trim * self.joystick_trim_follow_gain_virtual_x)
        self.cyclic_virtual_trim_y_offs = cyclic_y_trim - (cyclic_y_trim * self.joystick_trim_follow_gain_virtual_y)


    def msfs_update_pedals(self, telem_data):
        if telem_data.get("FFBType") != 'pedals':
            return

        if self.telemffb_controls_axes and not self.local_disable_axis_control:
            input_data = HapticEffect.device.get_input()
            phys_x, phys_y = input_data.axisXY()
            x_scale = clamp(self.rudder_x_axis_scale, 0.0, 1.0)

            self._spring_handle.name = "pedal_spring"
            # self.damper = effects["pedal_damper"].damper()

            pedal_pos = telem_data.get("TailRotorPedalPos")
            input_data = HapticEffect.device.get_input()
            phys_x, phys_y = input_data.axisXY()
            telem_data['phys_x'] = phys_x

            if self._sim_is_msfs():
                if (self.custom_ft_sw_var_enabled and self.anything_has_changed('custom_ft_sw_var', self.custom_ft_sw_var)) or self.anything_has_changed('custom_ft_sw_var_enabled', self.custom_ft_sw_var_enabled):
                    self._simconnect.add_simvar(name='ForceTrimSW', var=self.custom_ft_sw_var, sc_unit="enum")
                    self._simconnect._resubscribe()

            force_trim_active = telem_data.get('ForceTrimSW', True) if self.custom_ft_sw_var_enabled else True  # Enable cockpit switch control (if exists) for force trim.  Add LVar as "ForceTrimSW" bool if available for aircraft

            if not self.pedals_init:

                self.spring_x.set_coefficient(self.pedal_spring_coeff_x)
                if telem_data.get("SimOnGround", 1):
                    self.cpO_x = 0
                    self.last_pos_x_pos = 0
                else:
                    # print(f"last_colelctive_y={self.last_collective_y}")
                    self.cpO_x = round(4096 * self.last_pedal_x)

                self.spring_x.set_coefficient(self.pedal_spring_gain, True)
                self.spring_x.set_offset(self.cpO_x)

                self._spring_handle.setCondition(self.spring_x)
                # self.damper.damper(coef_x=int(4096 * self.pedal_dampening_gain)).start()
                self._spring_handle.start()
                logging.debug(f"self.cpO_x:{self.cpO_x}, phys_x:{phys_x}")
                if self.cpO_x / 4096 - 0.1 < phys_x < self.cpO_x / 4096 + 0.1:
                    # dont start sending position until physical pedals have centered
                    self.pedals_init = 1
                    logging.info("Pedals Initialized")
                    if not self.spring_mode_is(SpringModeEnum.FORCETRIM):
                        self._spring_handle.stop()
                else:
                    if self._sim_is_msfs():
                        if self.enable_custom_x_axis:
                            x_var = self.custom_x_axis
                        else:
                            x_var = 'ROTOR_AXIS_TAIL_ROTOR_SET'

                        self._simconnect.send_event_to_msfs(x_var, self.last_pos_x_pos)
                    return

            if self.spring_mode_is(SpringModeEnum.FORCETRIM):
                if not self.ac_update_pedal_force_trim(telem_data, ft_active=force_trim_active):
                    self.spring_x.set_coefficient(self.pedal_spring_gain, True)
                self._spring_handle.setCondition(self.spring_x)
            else:
                self.spring_x.set_coefficient(0)
                self._spring_handle.setCondition(self.spring_x)

            if not self._spring_handle.started:
                self._spring_handle.start()

            self.last_pedal_x = phys_x

            if self._sim_is_xplane():
                pos_x_pos = phys_x * x_scale
                self.send_xp_command(f'AXIS:px={round(pos_x_pos, 5)}')
            if self._sim_is_msfs():
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

                self._simconnect.send_event_to_msfs(x_var, pos_x_pos)
                self.last_pos_x_pos = pos_x_pos

    def msfs_update_collective(self, telem_data):
        if not self.is_collective():
            return
        if not self.telemffb_controls_axes and not self.local_disable_axis_control:
            return
        input_data = HapticEffect.device.get_input()
        phys_x, phys_y = input_data.axisXY()

        telem_data['phys_y'] = phys_y
        if not self.collective_init:
            self._spring_handle.name = "collective_ap_spring"
            self.spring_y.set_coefficient(4096)
            if self._sim_is_msfs():
                if self.enable_custom_y_axis:
                    y_var = self.custom_y_axis
                    y_range = self.raw_y_axis_scale
                else:
                    y_var = 'AXIS_COLLECTIVE_SET'
                    y_range = 16384
            if telem_data.get("SimOnGround", 1):
                # aircraft is on ground, so initialize collective to full down
                self.cpO_y = 4096
                if self._sim_is_msfs():
                    if y_range != 1:
                        self.last_pos_y_pos = -y_range * 1
                    else:
                        self.last_pos_y_pos = 1
            elif self.last_collective_y is None:
                # Air start or new aircraft.  Use current physical position as init point
                self.cpO_y = round(4096 * phys_y)
            else:
                # In air, previously paused.  Use stored collective position to init point
                self.cpO_y = round(4096 * self.last_collective_y)

            self.spring_y.set_coefficient(4096)
            self.spring_y.cpOffset = self.cpO_y

            self._spring_handle.setCondition(self.spring_y)
            # self.damper.damper(coef_y=int(4096*self.collective_dampening_gain)).start()
            self._spring_handle.start(override=True)
            # print(f"self.cpO_y:{self.cpO_y}, phys_y:{phys_y}")
            if self.cpO_y/4096 - 0.1 < phys_y < self.cpO_y/4096 + 0.1:
                # dont start sending position until physical stick has centered
                self.collective_init = 1
                logging.info("Collective Initialized")
            else:
                if self._sim_is_msfs():
                    self._simconnect.send_event_to_msfs(y_var, self.last_pos_y_pos)

                return
        self.last_collective_y = phys_y

        if self.spring_mode_is(SpringModeEnum.FORCETRIM):
            self._spring_handle.name = "collective_ft"
            self.ac_collective_force_trim_override(telem_data, self._spring_handle)
        else:
            self._spring_handle.name = "collective_ap_spring"

            self.cpO_y = round(4096 * utils.clamp(phys_y, -1, 1))
            # print(self.cpO_y)
            self.spring_y.cpOffset = self.cpO_y

            # self.damper.damper(coef_y=int(4096*self.collective_dampening_gain)).start()
            self.spring_y.set_coefficient(round(self.collective_spring_coeff_y / 2))

            self._spring_handle.setCondition(self.spring_y)
            self._spring_handle.start(override=True)

        if self._sim_is_xplane():
            pos_y_pos = utils.scale(phys_y, (-1, 1), (1, 0))
            if self.collective_init:
                self.send_xp_command(f'AXIS:cy={round(pos_y_pos, 5)}')

        if self._sim_is_msfs():
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
                self.last_pos_y_pos = pos_y_pos