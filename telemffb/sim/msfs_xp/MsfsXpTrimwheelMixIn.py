import telemffb.utils as utils
from telemffb.hw.ffb_rhino import HapticEffect
from telemffb.sim.msfs_xp.MsfsXpFlightControlsMixIn import MsfsXpFlightControlsMixIn


import logging
from telemffb.sim.BaseTelemetryData import BaseTelemetryData


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

    # NOTE: no on_telemetry hook here. Trimwheel devices are single-purpose:
    # Aircraft.on_telemetry gates them BEFORE the cooperative effects chain
    # and calls msfs_update_trimwheel directly, so no haptic effect can play
    # through the wheel.

    def msfs_update_trimwheel(self, telem_data: BaseTelemetryData):
        """Drive trimwheel spring position from sim elevator trim and send axis to sim.

        Telemetry:
            Read (MSFS):   APMaster       - Union[bool, int] (0 or 1); autopilot engaged state
            Read (XPLANE): APServos       - Union[bool, int] (0 or 1); autopilot servo state
            Read: ElevTrim         - float (degrees); raw elevator trim angle; non-axis mode only
                  ElevTrimMax      - float (degrees); upper trim travel limit; primary source
                  ElevTrimUpLmt    - float (degrees); fallback upper trim limit
                  ElevTrimMin      - float (degrees); lower trim travel limit; primary source
                  ElevTrimDnLmt    - float (degrees); fallback lower trim limit
                  ElevTrimNeutral  - float (degrees); neutral trim position; NOT USED in any
                                     active code path (non-axis path always raises ValueError)
                  ElevTrimPct      - float (–1.0 to 1.0); normalized elevator trim pct;
                                     used in axis mode and for spring initialization
            Written: trimwheel_pos_calc (float, –1.0 to 1.0; scaled trimwheel position)
                     phys_y             (float, –1.0 to 1.0; raw physical Y axis position)
                     _tw_cpO_y          (float; spring center offset, normalized)
                     _tw_phys_y_pos     (float, –1.0 to 1.0; physical Y before scaling)
                     _tw_phys_y_pos_neg (float; negated physical Y)
                     _tw_pos_y_pos      (int or float; scaled position sent to MSFS)
                     TRIM_DELTA         (float; delta between physical and trim positions)
                     _tw_last           (int or float; last position sent to sim)

        Note: ElevTrimNeutral is fetched but the non-axis code path is unreachable
              (the else branch raises ValueError), so it is effectively unused.
        """
        if not self.is_trimwheel():
            return
        if not self.telemffb_controls_axes and not self.local_disable_axis_control:
            return
        
        assert HapticEffect.device is not None, "Haptic device not initialized"
        
        ap_active = 0
        if self._sim_is_msfs():
            ap_active = telem_data.APMaster or 0
        if self._sim_is_xplane():
            ap_active = telem_data.APServos or 0

        input_data = HapticEffect.device.get_input()
        phys_x, phys_y = self._get_device_axes()
        self._spring_handle.name = "trimwheel_ap_spring"
        if not self.trimwheel_use_axis:
            trim_pos = telem_data.ElevTrim

            trim_limit_max = telem_data.ElevTrimMax
            if trim_limit_max == None:
                trim_limit_max = telem_data.ElevTrimUpLmt

            trim_limit_min = telem_data.ElevTrimMin
            if trim_limit_min == None:
                trim_limit_min = telem_data.ElevTrimDnLmt
                # trim_limit_min = -trim_limit_max
            trim_limit_neutral = telem_data.ElevTrimNeutral
            # linear scale
            trimwheel_pos = utils.scale(trim_pos, (trim_limit_min, trim_limit_max), (-1, 1))

            #     if phys_y > 0:
            #         trimwheel_pos = utils.scale(trim_pos, (trim_limit_neutral, trim_limit_up), (0, 1))
            #     else:
            #         trimwheel_pos = utils.scale(trim_pos, (-trim_limit_down, trim_limit_neutral), (-1, 0))

            telem_data.trimwheel_pos_calc = trimwheel_pos

        # print(f"Linear:{round(phys_y, 4)}, Curved:{round(trimwheel_pos, 4)}")

        telem_data.phys_y = phys_y
        if not self.trimwheel_init:
            self.spring_y.set_coefficient(1.0)

            if self.last_trimwheel_y is None:
                # Air start or new aircraft.  Use sim defined trim setpoint as init point
                trimwheel_pos = telem_data.ElevTrimPct
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
            trimwheel_pos = telem_data.ElevTrimPct or 0
        else:
            raise ValueError("Trimwheel must use axis")

        if ap_active == 0:

            # trimwheel_pos = self.dampener.dampen_value(trimwheel_pos, '_elev_trim', derivative_hz=5, derivative_k=0.15)
            self.cpO_y = round(utils.scale(trimwheel_pos, (-1, 1), (-4096, 4096)))
            telem_data._tw_cpO_y = trimwheel_pos
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
                telem_data._tw_phys_y_pos = phys_y
                telem_data._tw_phys_y_pos_neg = -phys_y
                telem_data._tw_pos_y_pos = pos_y_pos
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
                telem_data.TRIM_DELTA = delta
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
                telem_data._tw_last = self.last_pos_y_pos

        else:
            # trimwheel_pos = self.dampener.dampen_value(trimwheel_pos, '_elev_trim', derivative_hz=5, derivative_k=0.15)
            self.spring_y.set_offset(trimwheel_pos)
            # self.damper.damper(coef_y=0).start()
            self.spring_y.set_coefficient(self.trimwheel_ap_spring_gain, True)

            self._spring_handle.setCondition(self.spring_y)
            self._spring_handle.start(override=True)
