"""The force frame calibrator's device interface over the DirectInput
device this instance drives."""
import logging
from typing import Optional

from telemffb.calibration.force_frame import Axes, AxisSpring
from telemffb.hw.ffb_dinput import ForceFrame
from telemffb.hw.ffb_rhino import FFBReport_SetCondition, HapticEffect
from telemffb.utils import Dispenser


class LiveCalibrationDevice:
    """``CalibrationDevice`` for a connected ``DInputFFBDevice``.

    The effects it plays live in a table of their own, never the aircraft
    table, and ``release`` frees them.  The device must be the one
    ``HapticEffect`` renders on: effects reach the hardware through that
    class-level device.
    """

    SPRING = "force_frame_cal_spring"
    CONSTANT = "force_frame_cal_constant"

    def __init__(self, device):
        self._device = device
        self._effects = Dispenser(HapticEffect)

    def read_axes(self) -> Optional[Axes]:
        report = self._device.get_input()
        if report is None:
            return None
        return report.axisXY()

    def set_frame(self, frame: Optional[ForceFrame]) -> None:
        self._device.set_force_frame_override(frame)

    def spring(self, x: Optional[AxisSpring], y: Optional[AxisSpring]) -> None:
        effect = self._effects[self.SPRING]
        effect.spring(coef_x=float(x.coefficient if x else 0.0),
                      coef_y=float(y.coefficient if y else 0.0),
                      sat_x=float(x.saturation if x else 0.0),
                      sat_y=float(y.saturation if y else 0.0))
        for axis, block in ((0, x), (1, y)):
            if block is None or not block.offset:
                continue
            cond = FFBReport_SetCondition(parameterBlockOffset=axis)
            cond.set_offset(float(block.offset))
            cond.set_coefficient(float(block.coefficient))
            cond.set_saturation(float(block.saturation))
            effect.setCondition(cond)
        effect.start()

    def constant(self, magnitude: float, direction_deg: int) -> None:
        if not magnitude:
            if self.CONSTANT in self._effects:
                self._effects[self.CONSTANT].stop(destroy_after=0)
            return
        self._effects[self.CONSTANT].constant(float(magnitude), direction_deg).start()

    def stop_all(self) -> None:
        for name in list(self._effects):
            self._effects[name].stop(destroy_after=0)

    def release(self) -> None:
        try:
            self._effects.clear()
        except Exception:
            logging.exception("force frame calibration effects were not freed")
