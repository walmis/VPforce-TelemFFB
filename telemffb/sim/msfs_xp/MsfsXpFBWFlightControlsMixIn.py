import telemffb.utils as utils
from telemffb.hw.ffb_rhino import HapticEffect
from telemffb.sim.aircraft_base import AircraftBase
from telemffb.sim.base.AircraftEffectUtilsBase  import AircraftEffectUtilsBase

from telemffb.sim.msfs_xp.MsfsXpSimConnectMixIn import MsfsXpSimConnectMixIn
from telemffb.sim.base.AdvancedSpringMixIn import AdvancedSpringMixIn

from telemffb.utils import clamp


import logging


class MsfsXpFBWFlightControlsMixIn(AdvancedSpringMixIn, MsfsXpSimConnectMixIn):
    """Mixin for MSFS FBW flight control handling."""

    # user parameters
    rudder_trim_follow_gain_physical_x = 1.0
    rudder_trim_follow_gain_virtual_x = 0.2
    rudder_x_axis_scale = 1.0

    joystick_trim_follow_gain_physical_x = 1.0
    joystick_trim_follow_gain_physical_y = 0.2
    joystick_trim_follow_gain_virtual_x = 1.0
    joystick_trim_follow_gain_virtual_y = 0.2

    joystick_ap_follow_gain_physical_x = 1.0
    joystick_ap_follow_gain_physical_y = 0.5
    joystick_ap_follow_gain_virtual_x = 1.0
    joystick_ap_follow_gain_virtual_y = 0.5
    trim_following = False

    joystick_ap_y_follow_axis = False

    joystick_ap_x_follow_deadzone = 0.05
    joystick_ap_y_follow_deadzone = 0.05

    joystick_x_axis_scale = 1.0
    joystick_y_axis_scale = 1.0

    ap_following = True

    invert_ap_x_axis = False

    enable_custom_x_axis = False
    enable_custom_y_axis = False

    custom_x_axis: str = ""
    custom_y_axis: str = ""
    raw_x_axis_scale: int = 16384
    raw_y_axis_scale: int = 16384

    aircraft_is_fbw = 0  # deprecated
    fbw_elevator_gain = 0.8
    fbw_aileron_gain = 0.8
    fbw_rudder_gain = 0.8
    # end of user parameters

    def _update_fbw_flight_controls(self, telem_data, ap=False):
        ap_send_flag_x = True
        ap_send_flag_y = True
        ffb_type = telem_data.get("FFBType", "joystick")
        if self._sim_is_msfs():
            ap_active = telem_data.get("APMaster", 0)
        elif self._sim_is_xplane():
            ap_active = telem_data.get("APServos", 0)
        else:
            ap_active = 0

        spr_name = "ap_spring" if ap else "fbw_spring"
        self._spring_handle.name = spr_name

        if ffb_type == "joystick":

            if self.trim_following and self.telemffb_controls_axes and not self.local_disable_axis_control:

                elev_trim = telem_data.get("ElevTrimPct", 0)

                elev_trim = self.dampener.dampen_value(elev_trim, "_elev_trim", derivative_hz=5, derivative_k=0.15)

                # print(f"raw:{raw_elev_trim}, smooth:{elev_trim}")
                aileron_trim = telem_data.get("AileronTrimPct", 0)

                aileron_trim = clamp(aileron_trim * self.joystick_trim_follow_gain_physical_x, -1, 1)
                virtual_stick_x_offs = aileron_trim - (aileron_trim * self.joystick_trim_follow_gain_virtual_x)

                elev_trim = clamp(elev_trim * self.joystick_trim_follow_gain_physical_y, -1, 1)
                virtual_stick_y_offs = elev_trim - (elev_trim * self.joystick_trim_follow_gain_virtual_y)

                phys_stick_y_offs = int(elev_trim * 4096)

                if self.ap_following and ap_active:
                    input_data = HapticEffect.device.get_input()
                    phys_x, phys_y = input_data.axisXY()
                    if self._sim_is_msfs():
                        aileron_pos = telem_data.get("AileronDeflPctLR", (0, 0))
                        telem_data["phys_x_aileron"] = aileron_pos[0]
                        if self.joystick_ap_y_follow_axis:
                            elevator_pos = telem_data.get("ElevDeflPct", 0)
                        else:
                            elevator_pos = telem_data.get("ElevTrimPct", 0)

                        elevator_pos = telem_data.get("ElevTrimPct", 0)
                        elevator_pos = self.dampener.dampen_value(
                            elevator_pos, "_elev_trim", derivative_hz=5, derivative_k=0.15
                        )
                        elevator_pos = clamp(elevator_pos * self.joystick_ap_follow_gain_physical_y, -1, 1)
                        virtual_stick_y_offs = elevator_pos - (elevator_pos * self.joystick_trim_follow_gain_virtual_y)
                        phys_stick_y_offs = int(elevator_pos * 4096)

                        aileron_pos = clamp(aileron_pos[0] * self.joystick_ap_follow_gain_physical_x, -1, 1)
                        # aileron_pos = self.dampener.dampen_value(aileron_pos, '_aileron_pos', derivative_hz=5, derivative_k=0.15)
                        virtual_stick_x_offs = aileron_pos - (aileron_pos * self.joystick_ap_follow_gain_virtual_x)

                        # if self.joystick_ap_y_follow_axis:
                        #     elevator_pos = clamp(elevator_pos * self.joystick_ap_follow_gain_physical_y, -1, 1)
                        #     virtual_stick_y_offs = elevator_pos - (elevator_pos * self.joystick_ap_follow_gain_virtual_y)
                        #     phys_stick_y_offs = round(elevator_pos * 4096)

                        ap_send_flag_x = (
                            True if abs(phys_x - aileron_pos) > self.joystick_ap_x_follow_deadzone else False
                        )
                        ap_send_flag_y = (
                            True if abs(phys_y - elevator_pos) > self.joystick_ap_y_follow_deadzone else False
                        )

                        telem_data["phys_x_send_flag"] = ap_send_flag_x
                        telem_data["phys_y_send_flag"] = ap_send_flag_y

                    if self._sim_is_xplane():
                        aileron_pos = telem_data.get("APRollServo", 0)
                        aileron_pos = self.dampener.dampen_value(
                            aileron_pos, "_aileron_pos", derivative_hz=5, derivative_k=0.15
                        )
                        elevator_pos = telem_data.get("APPitchServo", 0)
                        phys_stick_y_offs = int(elevator_pos * 4096)

                    phys_stick_x_offs = int(aileron_pos * 4096)
                    if self.invert_ap_x_axis:
                        phys_stick_x_offs = -phys_stick_x_offs
                else:
                    phys_stick_x_offs = int(aileron_trim * 4096)

            else:
                phys_stick_x_offs = 0
                virtual_stick_x_offs = 0
                phys_stick_y_offs = 0
                virtual_stick_y_offs = 0

            if self.telemffb_controls_axes and not self.local_disable_axis_control:
                input_data = HapticEffect.device.get_input()
                phys_x, phys_y = input_data.axisXY()
                telem_data["phys_x"] = phys_x
                telem_data["phys_y"] = phys_y
                x_pos = phys_x - virtual_stick_x_offs
                y_pos = phys_y - virtual_stick_y_offs
                telem_data["phys_x_pos"] = x_pos
                telem_data["phys_y_pos"] = y_pos
                x_scale = clamp(self.joystick_x_axis_scale, 0, 1)
                y_scale = clamp(self.joystick_y_axis_scale, 0, 1)
                if self._sim_is_xplane():
                    pos_x_pos = x_pos * x_scale
                    pos_y_pos = y_pos * y_scale
                    self.send_xp_command(f"AXIS:jx={round(pos_x_pos, 5)},jy={round(pos_y_pos, 5)}")

                if self._sim_is_msfs():
                    if self.enable_custom_x_axis:
                        x_var = self.custom_x_axis
                        x_range = self.raw_x_axis_scale
                    else:
                        x_var = "AXIS_AILERONS_SET"
                        x_range = 16384
                    if self.enable_custom_y_axis:
                        y_var = self.custom_y_axis
                        y_range = self.raw_y_axis_scale
                    else:
                        y_var = "AXIS_ELEVATOR_SET"
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

                    # Only send axis position if "send flags" are true
                    # "send_flags" will be false if autopilot is engaged and physical control is within deadzone
                    # once deadzone is breached for an axis, position will be sent
                    # if AP is not active, flags are always True
                    if ap_send_flag_x:
                        self._simconnect.send_event_to_msfs(x_var, pos_x_pos)
                    else:
                        self._simconnect.send_event_to_msfs(x_var, 0)
                    if ap_send_flag_y:
                        self._simconnect.send_event_to_msfs(y_var, pos_y_pos)
                    else:
                        self._simconnect.send_event_to_msfs(y_var, 0)

            # update spring data
            if self.ap_following and ap_active:
                y_coeff = 1.0
                x_coeff = 1.0
            else:
                y_coeff = clamp(self.fbw_elevator_gain, 0, 1.0)
                x_coeff = clamp(self.fbw_aileron_gain, 0, 1.0)

            self.spring_y.set_coefficient(y_coeff)
            # logging.debug(f"Elev Coeef: {elevator_coeff}")
            self.spring_y.cpOffset = phys_stick_y_offs

            self.spring_x.set_coefficient(x_coeff)
            self.spring_x.cpOffset = phys_stick_x_offs

            self._spring_handle.setCondition(self.spring_y)
            self._spring_handle.setCondition(self.spring_x)

        elif ffb_type == "pedals":
            if self.trim_following and self.telemffb_controls_axes and not self.local_disable_axis_control:

                rudder_trim = telem_data.get("RudderTrimPct", 0)

                rudder_trim = clamp(rudder_trim * self.rudder_trim_follow_gain_physical_x, -1, 1)
                virtual_rudder_x_offs = rudder_trim - (rudder_trim * self.rudder_trim_follow_gain_virtual_x)

                phys_rudder_x_offs = int(rudder_trim * 4096)

                if self.ap_following and ap_active:
                    input_data = HapticEffect.device.get_input()
                    # print("I am here")
                    phys_x, phys_y = input_data.axisXY()
                    rudder_pos = None
                    if self._sim_is_msfs():
                        rudder_pos = telem_data.get("RudderDeflPct", 0)
                    if self._sim_is_xplane():
                        rudder_pos = telem_data.get("APYawServo", 0)

                    rudder_pos = self.dampener.dampen_value(
                        rudder_pos, "_rudder_pos", derivative_hz=5, derivative_k=0.15
                    )
                    # derivative_hz = 5  # derivative lpf filter -3db Hz
                    # derivative_k = 0.1  # derivative gain value, or damping ratio
                    #
                    # d_rudder_pos = getattr(self, "_d_rudder_pos", None)
                    # if not d_rudder_pos: d_rudder_pos = self._d_rudder_pos = utils.Derivative(derivative_hz)
                    # d_rudder_pos.lpf.cutoff_freq_hz = derivative_hz
                    #
                    # rudder_pos_deriv = - d_rudder_pos.update(rudder_pos) * derivative_k
                    #
                    # rudder_pos += rudder_pos_deriv

                    phys_rudder_x_offs = int(rudder_pos * 4096)

                else:
                    phys_rudder_x_offs = int(rudder_trim * 4096)

            else:
                phys_rudder_x_offs = 0
                virtual_rudder_x_offs = 0

            if self.telemffb_controls_axes and not self.local_disable_axis_control:
                input_data = HapticEffect.device.get_input()
                phys_x, phys_y = input_data.axisXY()
                telem_data["phys_x"] = phys_x

                x_pos = phys_x - virtual_rudder_x_offs
                x_scale = clamp(self.rudder_x_axis_scale, 0, 1)

                if self._sim_is_xplane():
                    pos_x_pos = x_pos * x_scale
                    self.send_xp_command(f"AXIS:px={round(pos_x_pos, 5)} ")

                if self._sim_is_msfs():
                    if self.enable_custom_x_axis:
                        x_var = self.custom_x_axis
                        x_range = self.raw_x_axis_scale
                    else:
                        x_var = "AXIS_RUDDER_SET"
                        x_range = 16384

                    pos_x_pos = utils.scale(x_pos, (-1, 1), (-x_range * x_scale, x_range * x_scale))

                    if x_range != 1:
                        pos_x_pos = -int(pos_x_pos)
                    else:
                        pos_x_pos = round(pos_x_pos, 5)

                    self._simconnect.send_event_to_msfs(x_var, pos_x_pos)

                # update spring data

            if self.ap_following and ap_active:
                x_coeff = 4096
            else:
                x_coeff = clamp(int(4096 * self.fbw_rudder_gain), 0, 4096)

            self.spring_x.set_coefficient(x_coeff)
            # print(f"{phys_rudder_x_offs}")
            self.spring_x.cpOffset = phys_rudder_x_offs
            logging.debug(f"Elev Coeef: {x_coeff}")

            self._spring_handle.setCondition(self.spring_x)
        self._spring_handle.start()
