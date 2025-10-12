from typing import override
import telemffb.utils as utils
from telemffb.hw.ffb_rhino import HapticEffect
from telemffb.sim.msfs_xp.MsfsXpFlightControlsMixIn import MsfsXpFlightControlsMixIn


import logging


class MsfsXpTrimwheelMixIn(MsfsXpFlightControlsMixIn):
    """Mixin for MSFS and X-Plane specific trim wheel handling."""

    # user parameters
    trimwheel_use_axis: bool = False
    trimwheel_elev_up_button: int = 0
    trimwheel_elev_dn_button: int = 0
    trimwheel_use_master_buttons: bool = False
    trimwheel_axis_invert: bool = False
    trimwheel_ap_spring_gain = 1.0
    trimwheel_dampening_gain = 0.0
    # trimwheel_spring_coeff_y = 0.0  # not used
    # end of user parameters

    def __init__(self):
        super().__init__()
        self.trimwheel_init = 0
        self.last_trimwheel_y = None
        self.last_pos_y_pos = 0.0
        self.trim_active = False

    @override
    def on_telemetry(self, telem_data):
        super().on_telemetry(telem_data)
        self.msfs_update_trimwheel(telem_data)    
    

    def msfs_update_trimwheel(self, telem_data):
        if not self.is_trimwheel():
            return
        if not self.telemffb_controls_axes and not self.local_disable_axis_control:
            return
        
        ap_active = 0
        if self._sim_is_msfs():
            ap_active = telem_data.get("APMaster", 0)
        if self._sim_is_xplane():
            ap_active = telem_data.get("APServos", 0)

        input_data = HapticEffect.device.get_input()
        phys_x, phys_y = input_data.axisXY()
        self._spring_handle.name = "trimwheel_ap_spring"
        if not self.trimwheel_use_axis:
            trim_pos = telem_data.get("ElevTrim")

            trim_limit_max = telem_data.get("ElevTrimMax")
            if trim_limit_max == None:
                trim_limit_max = telem_data.get("ElevTrimUpLmt")

            trim_limit_min = telem_data.get("ElevTrimMin")
            if trim_limit_min == None:
                trim_limit_min = telem_data.get("ElevTrimDnLmt")
                # trim_limit_min = -trim_limit_max
            trim_limit_neutral = telem_data.get("ElevTrimNeutral")
            # linear scale
            trimwheel_pos = utils.scale(trim_pos, (trim_limit_min, trim_limit_max), (-1, 1))

            #     if phys_y > 0:
            #         trimwheel_pos = utils.scale(trim_pos, (trim_limit_neutral, trim_limit_up), (0, 1))
            #     else:
            #         trimwheel_pos = utils.scale(trim_pos, (-trim_limit_down, trim_limit_neutral), (-1, 0))

            telem_data["trimwheel_pos_calc"] = trimwheel_pos

        # print(f"Linear:{round(phys_y, 4)}, Curved:{round(trimwheel_pos, 4)}")

        telem_data["phys_y"] = phys_y
        if not self.trimwheel_init:
            self.spring_y.set_coefficient(1.0)

            if self.last_trimwheel_y is None:
                # Air start or new aircraft.  Use sim defined trim setpoint as init point
                trimwheel_pos = telem_data["ElevTrimPct"]
                self.cpO_y = round(4096 * trimwheel_pos)
            else:
                # In air, previously paused.  Use stored position to init point
                self.cpO_y = round(4096 * self.last_trimwheel_y)

            self.spring_y.set_coefficient(1.0)

            self.spring_y.cpOffset = self.cpO_y

            self._spring_handle.setCondition(self.spring_y)
            # self.damper.damper(coef_y=int(4096*self.trimwheel_dampening_gain)).start()
            self._spring_handle.start(override=True)
            # print(f"self.cpO_y:{self.cpO_y}, phys_y:{phys_y}")
            if self.cpO_y / 4096 - 0.1 < phys_y < self.cpO_y / 4096 + 0.1:
                # dont start sending position until physical stick has centered
                self.trimwheel_init = 1
                logging.info("Trim Wheel Initialized")
            else:
                # if self._sim_is_msfs():
                #     self._simconnect.send_event_to_msfs(y_var, self.last_trimwheel_y)
                return
        self.last_trimwheel_y = phys_y

        if self.trimwheel_use_axis:
            trimwheel_pos = telem_data.get("ElevTrimPct", 0)
        else:
            raise ValueError("Trimwheel must use axis")

        if ap_active == 0:

            # trimwheel_pos = self.dampener.dampen_value(trimwheel_pos, '_elev_trim', derivative_hz=5, derivative_k=0.15)
            self.cpO_y = round(utils.scale(trimwheel_pos, (-1, 1), (-4096, 4096)))
            telem_data["_tw_cpO_y"] = trimwheel_pos
            self.spring_y.set_offset(trimwheel_pos)

            # self.damper.damper(coef_y=0).start()
            self.spring_y.set_coefficient(self.trimwheel_ap_spring_gain, True)

            self._spring_handle.setCondition(self.spring_y)
            self._spring_handle.start(override=True)

            if self._sim_is_xplane():  # unknown if this works
                pos_y_pos = utils.scale(phys_y, (-1, 1), (1, 0))
                if self.trimwheel_init:
                    self.send_xp_command(f"AXIS:cy={round(pos_y_pos, 5)}")

            if self._sim_is_msfs():
                if self.enable_custom_y_axis:
                    y_var = self.custom_y_axis
                    y_range = self.raw_y_axis_scale
                else:
                    y_var = "AXIS_ELEV_TRIM_SET"
                    y_range = 16384

                input_data = HapticEffect.device.get_input()
                # phys_x, phys_y = input_data.axisXY()

                pos_y_pos = utils.scale(phys_y, (-1, 1), (-y_range, y_range))
                telem_data["_tw_phys_y_pos"] = phys_y
                telem_data["_tw_phys_y_pos_neg"] = -phys_y
                telem_data["_tw_pos_y_pos"] = pos_y_pos
                if y_range != 1:
                    pos_y_pos = -int(pos_y_pos)
                else:
                    pos_y_pos = round(pos_y_pos, 5)

                if self.check_button_press(
                    self.trimwheel_elev_up_button, self.trimwheel_use_master_buttons
                ) or self.check_button_press(self.trimwheel_elev_dn_button, self.trimwheel_use_master_buttons):
                    self.trim_active = True
                    return

                delta = round(phys_y - trimwheel_pos, 5)
                # utils.dbprint('yellow', f"delta: {delta}")
                telem_data["TRIM_DELTA"] = delta
                if self.trim_active:
                    if abs(delta) <= 0.003:
                        self.trim_active = False

                if not self.trim_active:
                    if self.trimwheel_use_axis:
                        if self.trimwheel_axis_invert:
                            pos_y_pos = -pos_y_pos
                            phys_y = -phys_y
                        self._simconnect.send_event_to_msfs(y_var, pos_y_pos)
                    else:

                        pos_y_pos = utils.scale(phys_y, (-1, 1), (trim_limit_min, trim_limit_max))
                        #    if phys_y > 0:
                        #        pos_y_pos = utils.scale(phys_y,(0, 1), (trim_limit_neutral, trim_limit_up))
                        #    else:
                        #        pos_y_pos = utils.scale(phys_y,(-1, 0), (-trim_limit_down, trim_limit_neutral))

                        pos_y_pos = pos_y_pos * 0.01745
                        self._simconnect.set_simdatum_to_msfs("ELEVATOR TRIM POSITION", pos_y_pos, units="radians")
                self.last_pos_y_pos = pos_y_pos
                telem_data["_tw_last"] = self.last_pos_y_pos

        else:
            # trimwheel_pos = self.dampener.dampen_value(trimwheel_pos, '_elev_trim', derivative_hz=5, derivative_k=0.15)
            self.spring_y.set_offset(trimwheel_pos)
            # self.damper.damper(coef_y=0).start()
            self.spring_y.set_coefficient(self.trimwheel_ap_spring_gain, True)

            self._spring_handle.setCondition(self.spring_y)
            self._spring_handle.start(override=True)
